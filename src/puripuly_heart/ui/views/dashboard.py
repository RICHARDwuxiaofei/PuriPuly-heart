import flet as ft

from puripuly_heart.core.language import get_all_language_options
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.glow import create_background_glow_stack
from puripuly_heart.ui.components.language_card import LanguageCard
from puripuly_heart.ui.components.language_modal import LanguageModal
from puripuly_heart.ui.components.power_button import PowerButton
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, language_name, t
from puripuly_heart.ui.theme import COLOR_TRANS_ON


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
        self.controls = [create_background_glow_stack(grid_content)]

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
