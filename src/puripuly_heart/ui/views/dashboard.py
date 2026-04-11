import flet as ft

from puripuly_heart.core.language import get_all_language_options
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.glow import create_background_glow_stack, create_glow_stack
from puripuly_heart.ui.components.language_card import LanguageCard
from puripuly_heart.ui.components.language_modal import LanguageModal
from puripuly_heart.ui.components.managed_trial_usage_bar import ManagedTrialUsageBar
from puripuly_heart.ui.components.power_button import PowerButton
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, language_name, t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_SURFACE,
    COLOR_TERTIARY,
    COLOR_TRANS_ON,
    COLOR_WARNING,
    get_card_shadow,
)


class DashboardView(ft.Column):
    """Main dashboard with widened K-2 shell layout."""

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
        self._managed_trial_remaining_percent: int | None = None

        # Current language settings
        self._source_lang_code = "ko"
        self._target_lang_code = "en"
        self._peer_source_lang_code = ""
        self._peer_target_lang_code = ""

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
        # Left-side control grid
        self.stt_button = PowerButton(
            label=t("dashboard.stt_label"),
            icon=ft.Icons.MIC,
            on_click=self._toggle_stt,
            icon_size=80,
            label_size=32,
        )
        self.peer_button = PowerButton(
            label=t("dashboard.peer_label"),
            icon=ft.Icons.RECORD_VOICE_OVER,
            on_click=self._noop_control_slot,
            icon_size=80,
            label_size=32,
            color_on=COLOR_WARNING,
        )
        self.trans_button = PowerButton(
            label=t("dashboard.trans_label"),
            icon=ft.Icons.TRANSLATE,
            on_click=self._toggle_translation,
            icon_size=80,
            label_size=32,
            color_on=COLOR_TRANS_ON,
        )
        self.overlay_button = PowerButton(
            label=t("dashboard.overlay_label"),
            icon=ft.Icons.VISIBILITY,
            on_click=self._noop_control_slot,
            icon_size=80,
            label_size=32,
            color_on=COLOR_TERTIARY,
        )

        # Right-side information stack
        self.display_card = DisplayCard(on_submit=self._on_submit)
        self.language_card = LanguageCard(
            on_self_source_click=self._open_source_dialog,
            on_self_target_click=self._open_target_dialog,
            on_self_swap_click=self._swap_languages,
            on_peer_source_click=self._open_peer_source_dialog,
            on_peer_target_click=self._open_peer_target_dialog,
            on_peer_swap_click=self._swap_peer_languages,
        )
        self.language_card.set_row_labels(
            t("dashboard.language.self"),
            t("dashboard.language.peer"),
        )
        self._refresh_language_card()
        self._update_input_font()

        top_controls = ft.Row(
            [
                ft.Container(content=self.stt_button, expand=True),
                ft.Container(content=self.peer_button, expand=True),
            ],
            spacing=16,
            expand=True,
        )
        bottom_controls = ft.Row(
            [
                ft.Container(content=self.trans_button, expand=True),
                ft.Container(content=self.overlay_button, expand=True),
            ],
            spacing=16,
            expand=True,
        )

        control_grid = ft.Column([top_controls, bottom_controls], spacing=16, expand=True)
        info_stack = ft.Column([self.display_card, self.language_card], spacing=16, expand=True)

        main_surface = ft.Row(
            [
                ft.Container(content=control_grid, expand=40),
                ft.Container(content=info_stack, expand=60),
            ],
            spacing=16,
            expand=True,
        )

        self._managed_trial_card = self._build_managed_trial_card()
        shell_content = ft.Column(
            [main_surface, self._managed_trial_card],
            spacing=16,
            expand=True,
        )
        self.controls = [create_background_glow_stack(shell_content)]
        self._sync_managed_trial_card(update_ui=False)

    def _build_managed_trial_card(self) -> ft.Container:
        self._managed_trial_badge = ft.Text(
            t("dashboard.trial.source.managed"),
            size=14,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.WHITE,
        )
        self._managed_trial_status_label = ft.Text(
            t("dashboard.trial.lifecycle_label"),
            size=13,
            color=COLOR_SECONDARY,
        )
        self._managed_trial_status_value = ft.Text(
            "",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
        )
        self._managed_trial_message_label = ft.Text(
            t("dashboard.trial.message_label"),
            size=13,
            color=COLOR_SECONDARY,
        )
        self._managed_trial_message_value = ft.Text(
            "",
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._managed_trial_usage_bar = ManagedTrialUsageBar()

        info_column = ft.Column(
            [
                ft.Container(
                    content=self._managed_trial_badge,
                    bgcolor=COLOR_PRIMARY,
                    border_radius=999,
                    padding=ft.padding.symmetric(horizontal=12, vertical=8),
                ),
                ft.Row(
                    [
                        ft.Column(
                            [self._managed_trial_status_label, self._managed_trial_status_value],
                            spacing=6,
                            expand=True,
                        ),
                        ft.Column(
                            [self._managed_trial_message_label, self._managed_trial_message_value],
                            spacing=6,
                            expand=True,
                        ),
                    ],
                    spacing=24,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=18,
            expand=True,
        )

        content = ft.Row(
            [
                info_column,
                ft.Container(content=self._managed_trial_usage_bar, alignment=ft.alignment.center),
            ],
            spacing=24,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Container(
            content=create_glow_stack(
                ft.Container(content=content, padding=24, alignment=ft.alignment.center_left)
            ),
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, COLOR_DIVIDER)),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
            visible=False,
        )

    def _managed_trial_lifecycle_key(self) -> str:
        if self._managed_trial_remaining_percent is None:
            return "dashboard.trial.lifecycle.pre_release"
        if self._managed_trial_remaining_percent == 0:
            return "dashboard.trial.lifecycle.exhausted"
        return "dashboard.trial.lifecycle.active"

    def _managed_trial_message_key(self) -> str:
        if self._managed_trial_remaining_percent is None:
            return "dashboard.trial.message.placeholder"
        if self._managed_trial_remaining_percent == 0:
            return "dashboard.trial.message.exhausted"
        return "dashboard.trial.message.live_usage"

    def _sync_managed_trial_card(self, *, update_ui: bool = True) -> None:
        self._managed_trial_badge.value = t("dashboard.trial.source.managed")
        self._managed_trial_status_label.value = t("dashboard.trial.lifecycle_label")
        self._managed_trial_message_label.value = t("dashboard.trial.message_label")
        self._managed_trial_status_value.value = t(self._managed_trial_lifecycle_key())
        self._managed_trial_message_value.value = t(self._managed_trial_message_key())
        self._managed_trial_usage_bar.set_percent(
            self._managed_trial_remaining_percent if self._managed_trial_visible else None
        )
        self._managed_trial_card.visible = self._managed_trial_visible
        if update_ui and self.page is not None:
            self._managed_trial_card.update()

    def _noop_control_slot(self) -> None:
        return None

    @property
    def managed_trial_state(self) -> dict[str, object]:
        return {
            "visible": self._managed_trial_visible,
            "remaining_percent": self._managed_trial_remaining_percent,
        }

    def set_managed_trial_state(
        self,
        *,
        visible: bool,
        remaining_percent: int | None = None,
        **_extra: object,
    ) -> None:
        self._managed_trial_visible = bool(visible)
        if self._managed_trial_visible and remaining_percent is not None:
            self._managed_trial_remaining_percent = max(0, min(100, int(remaining_percent)))
        else:
            self._managed_trial_remaining_percent = None
        self._sync_managed_trial_card()

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

    def _open_peer_source_dialog(self):
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=self._on_peer_source_select,
        )
        modal.open(
            current=self._effective_peer_source_lang_code(), recent=self._recent_source_langs
        )

    def _open_peer_target_dialog(self):
        modal = LanguageModal(
            page=self.page,
            languages=self._LANG_OPTIONS,
            on_select=self._on_peer_target_select,
        )
        modal.open(
            current=self._effective_peer_target_lang_code(), recent=self._recent_target_langs
        )

    def _on_source_select(self, lang_code: str):
        """Handle source language selection."""
        self._source_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=True)
        self._update_input_font()
        self._refresh_language_card()
        self._notify_language_change()

    def _on_target_select(self, lang_code: str):
        """Handle target language selection."""
        self._target_lang_code = lang_code
        self._add_to_recent(lang_code, is_source=False)
        self._refresh_language_card()
        self._notify_language_change()

    def _on_peer_source_select(self, lang_code: str):
        self._peer_source_lang_code = "" if lang_code == self._source_lang_code else lang_code
        self._add_to_recent(lang_code, is_source=True)
        self._refresh_language_card()
        self._notify_language_change()

    def _on_peer_target_select(self, lang_code: str):
        self._peer_target_lang_code = "" if lang_code == self._target_lang_code else lang_code
        self._add_to_recent(lang_code, is_source=False)
        self._refresh_language_card()
        self._notify_language_change()

    def _swap_languages(self):
        """Swap source and target languages."""
        self._source_lang_code, self._target_lang_code = (
            self._target_lang_code,
            self._source_lang_code,
        )
        self._update_input_font()
        self._refresh_language_card()
        self._notify_language_change()

    def _swap_peer_languages(self):
        current_peer_source = self._effective_peer_source_lang_code()
        current_peer_target = self._effective_peer_target_lang_code()
        self._peer_source_lang_code = current_peer_target
        self._peer_target_lang_code = current_peer_source
        self._refresh_language_card()
        self._notify_language_change()

    def _add_to_recent(self, lang_code: str, is_source: bool) -> None:
        """Add language to recent list, maintaining max 6 unique entries."""
        recent = self._recent_source_langs if is_source else self._recent_target_langs
        if lang_code in recent:
            recent.remove(lang_code)
        recent.insert(0, lang_code)
        if len(recent) > 6:
            recent.pop()
        if self.on_recent_languages_change:
            self.on_recent_languages_change(self._recent_source_langs, self._recent_target_langs)

    def _notify_language_change(self):
        if self.on_language_change:
            self.on_language_change(
                self._source_lang_code,
                self._target_lang_code,
                self._peer_source_lang_code,
                self._peer_target_lang_code,
            )

    def _effective_peer_source_lang_code(self) -> str:
        return self._peer_source_lang_code or self._source_lang_code

    def _effective_peer_target_lang_code(self) -> str:
        return self._peer_target_lang_code or self._target_lang_code

    def _refresh_language_card(self) -> None:
        self.language_card.set_languages(
            language_name(self._source_lang_code),
            language_name(self._target_lang_code),
            language_name(self._effective_peer_source_lang_code()),
            language_name(self._effective_peer_target_lang_code()),
        )

    def set_status(self, status: str) -> None:
        self.is_connected = status == "connected"
        self.display_card.set_status(status, font_family=self._ui_font())

    def set_languages_from_codes(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
    ) -> None:
        self._source_lang_code = source_code
        self._target_lang_code = target_code
        self._peer_source_lang_code = peer_source_code
        self._peer_target_lang_code = peer_target_code
        self._update_input_font()
        self._refresh_language_card()

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

    def apply_locale(self) -> None:
        self.stt_button.set_label(t("dashboard.stt_label"))
        self.peer_button.set_label(t("dashboard.peer_label"))
        self.trans_button.set_label(t("dashboard.trans_label"))
        self.overlay_button.set_label(t("dashboard.overlay_label"))
        self.display_card.apply_locale(
            display_font_family=self._ui_font(),
            input_font_family=font_for_language(self._source_lang_code),
        )
        self.language_card.set_row_labels(
            t("dashboard.language.self"),
            t("dashboard.language.peer"),
        )
        self._refresh_language_card()
        if self._stt_showing_warning:
            self.set_display_text(t("dashboard.warn_stt_key"))
        elif self._translation_showing_warning:
            self.set_display_text(t("dashboard.warn_llm_key"))
        self.set_local_stt_notice(
            self._local_stt_notice_status,
            percent=self._local_stt_notice_percent,
        )
        self._managed_trial_usage_bar.apply_locale()
        self._sync_managed_trial_card(update_ui=False)

    def set_recent_languages(self, source: list[str], target: list[str]) -> None:
        """Set recent languages from settings (for persistence)."""
        self._recent_source_langs = list(source)
        self._recent_target_langs = list(target)
        self._recent_source_langs = self._recent_source_langs[:6]
        self._recent_target_langs = self._recent_target_langs[:6]

    def _update_input_font(self) -> None:
        self.display_card.set_input_font(font_for_language(self._source_lang_code))

    def _ui_font(self) -> str | None:
        return font_for_language(get_locale())
