import flet as ft

from puripuly_heart.core.language import get_all_language_options
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.glow import create_background_glow_stack
from puripuly_heart.ui.components.language_card import LanguageCard
from puripuly_heart.ui.components.language_modal import LanguageModal
from puripuly_heart.ui.components.power_button import PowerButton
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, language_name, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    COLOR_TRANS_ON,
    get_card_shadow,
)


def _format_usd(value: float | None) -> str:
    if value is None:
        return t("dashboard.trial.usage.placeholder")
    return f"${value:.2f}"


class DashboardView(ft.Column):
    """Main dashboard with 2x2 asymmetric grid layout."""

    _LANG_OPTIONS = get_all_language_options()

    def __init__(self):
        super().__init__(expand=True, spacing=16)

        # State
        self.is_connected = False
        self.is_power_on = False
        self.is_translation_on = False
        self.is_stt_on = False
        self.translation_needs_key = False
        self.stt_needs_key = False
        self.last_sent_text = t("dashboard.ready")
        self.history_items = []

        # Warning state for UI feedback
        self._translation_showing_warning = False
        self._stt_showing_warning = False
        self._local_stt_notice_status: str | None = None
        self._local_stt_notice_percent: int | None = None
        self._managed_trial_visible = False
        self._managed_trial_lifecycle = "pre_release"
        self._managed_trial_transient_message_key: str | None = None
        self._managed_trial_transient_message_kwargs: dict[str, object] = {}
        self._managed_trial_usage_limit_usd: float | None = None
        self._managed_trial_usage_remaining_usd: float | None = None
        self._managed_trial_usage_used_usd: float | None = None

        # Current language settings
        self._source_lang_code = "ko"
        self._target_lang_code = "en"

        # Recent languages (max 3 each)
        self._recent_source_langs: list[str] = []
        self._recent_target_langs: list[str] = []

        # Callbacks (assigned by App)
        self.on_send_message = None
        self.on_toggle_translation = None
        self.on_toggle_stt = None
        self.on_language_change = None
        self.on_recent_languages_change = None  # For persistence

        self._build_ui()

    def _build_ui(self):
        # A: STT button (top-left) - larger icon
        self.stt_button = PowerButton(
            label=t("dashboard.stt_label"),
            icon=ft.Icons.MIC,
            on_click=self._toggle_stt,
            icon_size=96,
            label_size=36,
        )

        # B: Display card (top-right)
        self.display_card = DisplayCard(on_submit=self._on_submit)

        # C: TRANS button (bottom-left) - slightly smaller
        self.trans_button = PowerButton(
            label=t("dashboard.trans_label"),
            icon=ft.Icons.TRANSLATE,
            on_click=self._toggle_translation,
            icon_size=64,
            label_size=28,
            color_on=COLOR_TRANS_ON,
        )

        # D: Language card (bottom-right)
        self.language_card = LanguageCard(
            on_source_click=self._open_source_dialog,
            on_target_click=self._open_target_dialog,
            on_swap_click=self._swap_languages,
        )
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )
        self._update_input_font()

        # 2x2 Grid layout (35:65 ratio)
        top_row = ft.Row(
            [
                ft.Container(content=self.stt_button, expand=35),
                ft.Container(content=self.display_card, expand=65),
            ],
            spacing=16,
            expand=True,
        )

        bottom_row = ft.Row(
            [
                ft.Container(content=self.trans_button, expand=35),
                ft.Container(content=self.language_card, expand=65),
            ],
            spacing=16,
            expand=True,
        )

        # Wrap grid in background glow for atmospheric warmth
        grid_content = ft.Column(
            [top_row, bottom_row],
            spacing=16,
            expand=True,
        )

        self._managed_trial_title = ft.Text(
            "",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLOR_ON_BACKGROUND,
        )
        self._managed_trial_source = ft.Container(
            content=ft.Text("", size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
            bgcolor=COLOR_PRIMARY,
            border_radius=999,
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )
        self._managed_trial_lifecycle_label = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._managed_trial_lifecycle_value = ft.Text("", size=14, color=COLOR_ON_BACKGROUND)
        self._managed_trial_message_label = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._managed_trial_message_value = ft.Text("", size=14, color=COLOR_ON_BACKGROUND)
        self._managed_trial_transient_label = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._managed_trial_transient_value = ft.Text("", size=14, color=COLOR_ON_BACKGROUND)
        self._managed_trial_transient_row = ft.Row(
            [self._managed_trial_transient_label, self._managed_trial_transient_value],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            visible=False,
        )
        self._managed_trial_progress = ft.ProgressBar(
            value=0,
            bar_height=8,
            bgcolor=ft.Colors.with_opacity(0.2, COLOR_DIVIDER),
            color=COLOR_PRIMARY,
        )
        self._managed_trial_used_label = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._managed_trial_used_value = ft.Text("", size=14, color=COLOR_ON_BACKGROUND)
        self._managed_trial_remaining_label = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._managed_trial_remaining_value = ft.Text("", size=14, color=COLOR_ON_BACKGROUND)
        self._managed_trial_card = ft.Container(
            visible=False,
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            shadow=get_card_shadow(),
            padding=24,
            content=ft.Column(
                [
                    ft.Row(
                        [self._managed_trial_title, self._managed_trial_source],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [self._managed_trial_lifecycle_label, self._managed_trial_lifecycle_value],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [self._managed_trial_message_label, self._managed_trial_message_value],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._managed_trial_transient_row,
                    self._managed_trial_progress,
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    self._managed_trial_used_label,
                                    self._managed_trial_used_value,
                                ],
                                spacing=4,
                            ),
                            ft.Column(
                                [
                                    self._managed_trial_remaining_label,
                                    self._managed_trial_remaining_value,
                                ],
                                spacing=4,
                                horizontal_alignment=ft.CrossAxisAlignment.END,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=12,
            ),
        )
        self._sync_managed_trial_card()
        self.controls = [create_background_glow_stack(grid_content), self._managed_trial_card]

    def _toggle_stt(self):
        if self.is_stt_on:
            self.is_stt_on = False
            self._stt_showing_warning = False
            self.stt_button.set_state(False, needs_key=False)
        elif self._stt_showing_warning:
            self._stt_showing_warning = False
            self.stt_button.set_state(False, needs_key=False)
        elif self.stt_needs_key:
            self._stt_showing_warning = True
            self.stt_button.set_state(False, needs_key=True)
            self.set_display_text(t("dashboard.warn_stt_key"))
        else:
            self.is_stt_on = True
            self.stt_button.set_state(True)

        if self.on_toggle_stt:
            self.on_toggle_stt(self.is_stt_on)

    def _toggle_translation(self):
        if self.is_translation_on:
            self.is_translation_on = False
            self._translation_showing_warning = False
            self.trans_button.set_state(False, needs_key=False)
        elif self._translation_showing_warning:
            self._translation_showing_warning = False
            self.trans_button.set_state(False, needs_key=False)
        elif self.translation_needs_key:
            self._translation_showing_warning = True
            self.trans_button.set_state(False, needs_key=True)
            self.set_display_text(t("dashboard.warn_llm_key"))
        else:
            self.is_translation_on = True
            self.trans_button.set_state(True)

        self.is_power_on = self.is_translation_on
        if self.on_toggle_translation:
            self.on_toggle_translation(self.is_translation_on)

    def _on_submit(self, text: str):
        self.set_display_text(text, language_code=self._source_lang_code)
        if self.on_send_message:
            self.on_send_message("You", text)

    def _open_source_dialog(self):
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=self._on_source_select,
        )
        modal.open(current=self._source_lang_code, recent=self._recent_source_langs)

    def _open_target_dialog(self):
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=self._on_target_select,
        )
        modal.open(current=self._target_lang_code, recent=self._recent_target_langs)

    def _on_source_select(self, lang_code: str):
        """Handle source language selection."""
        self._source_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=True)
        self._update_input_font()
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )
        self._notify_language_change()

    def _on_target_select(self, lang_code: str):
        """Handle target language selection."""
        self._target_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=False)
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )
        self._notify_language_change()

    def _swap_languages(self):
        """Swap source and target languages."""
        self._source_lang_code, self._target_lang_code = (
            self._target_lang_code,
            self._source_lang_code,
        )
        self._update_input_font()
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )
        self._notify_language_change()

    def _add_to_recent(self, lang_code: str, is_source: bool) -> None:
        """Add language to recent list, maintaining max 6 unique entries."""
        recent = self._recent_source_langs if is_source else self._recent_target_langs
        if lang_code in recent:
            recent.remove(lang_code)
        recent.insert(0, lang_code)
        if len(recent) > 6:
            recent.pop()
        # Notify for persistence
        if self.on_recent_languages_change:
            self.on_recent_languages_change(self._recent_source_langs, self._recent_target_langs)

    def _notify_language_change(self):
        if self.on_language_change:
            self.on_language_change(self._source_lang_code, self._target_lang_code)

    # Public API methods
    def set_status(self, status: str) -> None:
        self.is_connected = status == "connected"
        self.display_card.set_status(status, font_family=self._ui_font())

    def set_languages_from_codes(self, source_code: str, target_code: str) -> None:
        self._source_lang_code = source_code
        self._target_lang_code = target_code
        self._update_input_font()
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )

    def set_translation_enabled(self, enabled: bool) -> None:
        self.is_translation_on = bool(enabled)
        self.trans_button.set_state(self.is_translation_on)

    def set_stt_enabled(self, enabled: bool) -> None:
        self.is_stt_on = bool(enabled)
        self.stt_button.set_state(self.is_stt_on)

    def set_translation_needs_key(self, needs_key: bool, *, update_ui: bool = True) -> None:
        self.translation_needs_key = bool(needs_key)
        if update_ui and needs_key and not self.is_translation_on:
            self.trans_button.set_state(False, needs_key=True)

    def set_stt_needs_key(self, needs_key: bool, *, update_ui: bool = True) -> None:
        self.stt_needs_key = bool(needs_key)
        if update_ui and needs_key and not self.is_stt_on:
            self.stt_button.set_state(False, needs_key=True)

    def set_display_text(
        self,
        text: str,
        *,
        language_code: str | None = None,
        is_error: bool = False,
    ) -> None:
        """Update the display card primary line with new text."""
        font_family = font_for_language(language_code) if language_code else self._ui_font()
        self.display_card.set_display(text, is_error=is_error, font_family=font_family)

    def set_display_translation_text(
        self,
        text: str | None,
        *,
        language_code: str | None = None,
    ) -> None:
        """Update the display card translation line."""
        font_family = font_for_language(language_code) if language_code else self._ui_font()
        self.display_card.set_display_translation(text, font_family=font_family)

    def set_local_stt_notice(self, status: str | None, percent: int | None = None) -> None:
        self._local_stt_notice_status = status
        self._local_stt_notice_percent = percent if status == "downloading" else None
        if status is None:
            self.display_card.set_notice(None, None)
            return

        notice_key_by_status = {
            "missing": "dashboard.local_stt_notice_missing",
            "invalid": "dashboard.local_stt_notice_invalid",
            "downloading": "dashboard.local_stt_notice_downloading",
            "download_failed": "dashboard.local_stt_notice_download_failed",
        }
        tone_by_status = {
            "missing": "warning",
            "invalid": "warning",
            "downloading": "info",
            "download_failed": "error",
        }
        notice_key = notice_key_by_status.get(status)
        if notice_key is None:
            self.display_card.set_notice(None, None)
            return
        notice_text = (
            t("dashboard.local_stt_notice_downloading_progress", percent=percent)
            if status == "downloading" and percent is not None
            else t(notice_key)
        )
        self.display_card.set_notice(notice_text, tone_by_status.get(status))

    def _managed_trial_lifecycle_key(self) -> str:
        return f"dashboard.trial.lifecycle.{self._managed_trial_lifecycle}"

    def _managed_trial_message_key(self) -> str:
        message_by_lifecycle = {
            "pre_release": "dashboard.trial.message.placeholder",
            "pending_auth": "dashboard.trial.message.pending_auth",
            "active": "dashboard.trial.message.live_usage",
            "exhausted": "dashboard.trial.message.exhausted",
            "unavailable": "dashboard.trial.message.unavailable",
            "usage-unavailable": "dashboard.trial.message.usage-unavailable",
        }
        return message_by_lifecycle.get(
            self._managed_trial_lifecycle,
            "dashboard.trial.message.placeholder",
        )

    def _managed_trial_progress_value(self) -> float:
        limit = self._managed_trial_usage_limit_usd
        used = self._managed_trial_usage_used_usd
        remaining = self._managed_trial_usage_remaining_usd
        if isinstance(limit, (int, float)) and limit > 0:
            if isinstance(used, (int, float)):
                return max(0.0, min(1.0, used / limit))
            if isinstance(remaining, (int, float)):
                return max(0.0, min(1.0, 1.0 - (remaining / limit)))
        return 0.0

    def _sync_managed_trial_card(self) -> None:
        self._managed_trial_card.visible = self._managed_trial_visible
        self._managed_trial_title.value = t("provider.gemma4_free_trial")
        self._managed_trial_source.content.value = t("dashboard.trial.source.managed")
        self._managed_trial_lifecycle_label.value = t("dashboard.trial.lifecycle_label")
        self._managed_trial_lifecycle_value.value = t(self._managed_trial_lifecycle_key())
        self._managed_trial_message_label.value = t("dashboard.trial.message_label")
        self._managed_trial_message_value.value = t(self._managed_trial_message_key())
        transient_text = (
            t(
                self._managed_trial_transient_message_key,
                **self._managed_trial_transient_message_kwargs,
            )
            if self._managed_trial_transient_message_key
            else None
        )
        self._managed_trial_transient_label.value = t("dashboard.trial.transient_label")
        self._managed_trial_transient_value.value = transient_text or ""
        self._managed_trial_transient_row.visible = bool(transient_text)
        self._managed_trial_progress.value = self._managed_trial_progress_value()
        self._managed_trial_used_label.value = t("dashboard.trial.used_label")
        self._managed_trial_used_value.value = _format_usd(self._managed_trial_usage_used_usd)
        self._managed_trial_remaining_label.value = t("dashboard.trial.remaining_label")
        self._managed_trial_remaining_value.value = _format_usd(
            self._managed_trial_usage_remaining_usd
        )

    def set_managed_trial_state(
        self,
        *,
        visible: bool,
        lifecycle: str,
        transient_message_key: str | None = None,
        transient_message_kwargs: dict[str, object] | None = None,
        usage_limit_usd: float | None = None,
        usage_remaining_usd: float | None = None,
        usage_used_usd: float | None = None,
    ) -> None:
        self._managed_trial_visible = bool(visible)
        self._managed_trial_lifecycle = lifecycle
        self._managed_trial_transient_message_key = transient_message_key
        self._managed_trial_transient_message_kwargs = dict(transient_message_kwargs or {})
        self._managed_trial_usage_limit_usd = usage_limit_usd
        self._managed_trial_usage_remaining_usd = usage_remaining_usd
        self._managed_trial_usage_used_usd = usage_used_usd
        self._sync_managed_trial_card()

    def apply_locale(self) -> None:
        self.stt_button.set_label(t("dashboard.stt_label"))
        self.trans_button.set_label(t("dashboard.trans_label"))
        self.display_card.apply_locale(
            display_font_family=self._ui_font(),
            input_font_family=font_for_language(self._source_lang_code),
        )
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
        )
        if self._stt_showing_warning:
            self.set_display_text(t("dashboard.warn_stt_key"))
        elif self._translation_showing_warning:
            self.set_display_text(t("dashboard.warn_llm_key"))
        self.set_local_stt_notice(
            self._local_stt_notice_status,
            percent=self._local_stt_notice_percent,
        )
        self._sync_managed_trial_card()

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        """Set recent languages from settings (for persistence)."""
        self._recent_source_langs = list(source)
        self._recent_target_langs = list(target)
        # Keep only the last 6 unique languages
        self._recent_source_langs = self._recent_source_langs[:6]
        self._recent_target_langs = self._recent_target_langs[:6]

    def _update_input_font(self) -> None:
        self.display_card.set_input_font(font_for_language(self._source_lang_code))

    def _ui_font(self) -> str | None:
        return font_for_language(get_locale())
