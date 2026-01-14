import flet as ft

from puripuly_heart.core.language import get_all_language_options
from puripuly_heart.ui.components.display_card import DisplayCard
from puripuly_heart.ui.components.glow import create_background_glow_stack
from puripuly_heart.ui.components.language_card import LanguageCard
from puripuly_heart.ui.components.power_button import PowerButton
from puripuly_heart.ui.theme import COLOR_NEUTRAL_DARK


class DashboardView(ft.Column):
    """Main dashboard with 2x2 asymmetric grid layout."""

    _LANG_OPTIONS = get_all_language_options()
    LANG_LABEL_TO_CODE = {name: code for code, name in _LANG_OPTIONS}
    LANG_CODE_TO_LABEL = {code: name for code, name in _LANG_OPTIONS}

    def __init__(self):
        super().__init__(expand=True, spacing=16)

        # State
        self.is_connected = False
        self.is_power_on = False
        self.is_translation_on = False
        self.is_stt_on = False
        self.translation_needs_key = False
        self.stt_needs_key = False
        self.last_sent_text = "Ready to translate..."
        self.history_items = []

        # Warning state for UI feedback
        self._translation_showing_warning = False
        self._stt_showing_warning = False

        # Current language settings
        self._source_lang = "Korean"
        self._target_lang = "English"

        # Callbacks (assigned by App)
        self.on_send_message = None
        self.on_toggle_translation = None
        self.on_toggle_stt = None
        self.on_language_change = None

        self._build_ui()

    def _build_ui(self):
        # A: STT button (top-left) - larger icon
        self.stt_button = PowerButton(
            label="STT",
            icon=ft.Icons.MIC,
            on_click=self._toggle_stt,
            icon_size=96,
            label_size=36,
        )

        # B: Display card (top-right)
        self.display_card = DisplayCard(on_submit=self._on_submit)

        # C: TRANS button (bottom-left) - slightly smaller
        self.trans_button = PowerButton(
            label="TRANS",
            icon=ft.Icons.TRANSLATE,
            on_click=self._toggle_translation,
            icon_size=64,
            label_size=28,
        )

        # D: Language card (bottom-right)
        self.language_card = LanguageCard(
            on_source_click=self._open_source_dialog,
            on_target_click=self._open_target_dialog,
        )

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
            self.set_display_text("Please enter your STT API key")
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
            self.set_display_text("Please enter your LLM API key")
        else:
            self.is_translation_on = True
            self.trans_button.set_state(True)

        self.is_power_on = self.is_translation_on
        if self.on_toggle_translation:
            self.on_toggle_translation(self.is_translation_on)

    def _on_submit(self, text: str):
        self.display_card.set_display(text)
        if self.on_send_message:
            self.on_send_message("You", text)

    def _open_source_dialog(self):
        self._open_language_dialog(is_source=True)

    def _open_target_dialog(self):
        self._open_language_dialog(is_source=False)

    def _open_language_dialog(self, is_source: bool):
        """Open language selection dialog."""
        lang_names = [name for _, name in self._LANG_OPTIONS]
        title = "Source Language" if is_source else "Target Language"
        current = self._source_lang if is_source else self._target_lang

        # Preset buttons
        presets = [
            {"label": "KR \u2192 EN", "src": "Korean", "tgt": "English"},
            {"label": "EN \u2192 KR", "src": "English", "tgt": "Korean"},
            {"label": "KR \u2192 JP", "src": "Korean", "tgt": "Japanese"},
        ]

        def on_preset_click(e):
            preset = e.control.data
            self._source_lang = preset["src"]
            self._target_lang = preset["tgt"]
            self.language_card.set_languages(self._source_lang, self._target_lang)
            self._notify_language_change()
            self.page.close(dlg)

        def on_lang_select(e):
            lang = e.control.data
            if is_source:
                self._source_lang = lang
            else:
                self._target_lang = lang
            self.language_card.set_languages(self._source_lang, self._target_lang)
            self._notify_language_change()
            self.page.close(dlg)

        preset_row = ft.Row(
            [
                ft.TextButton(
                    text=p["label"],
                    data=p,
                    on_click=on_preset_click,
                )
                for p in presets
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )

        lang_list = ft.ListView(
            controls=[
                ft.ListTile(
                    title=ft.Text(name, color=COLOR_NEUTRAL_DARK),
                    data=name,
                    on_click=on_lang_select,
                    bgcolor=(
                        ft.Colors.with_opacity(0.1, ft.Colors.PRIMARY) if name == current else None
                    ),
                )
                for name in lang_names
            ],
            height=300,
        )

        dlg = ft.AlertDialog(
            title=ft.Text(title, weight=ft.FontWeight.BOLD),
            content=ft.Column([preset_row, ft.Divider(), lang_list], spacing=8, width=300),
        )

        self.page.open(dlg)

    def _notify_language_change(self):
        if self.on_language_change:
            source_code = self.LANG_LABEL_TO_CODE.get(self._source_lang, "ko")
            target_code = self.LANG_LABEL_TO_CODE.get(self._target_lang, "en")
            self.on_language_change(source_code, target_code)

    # Public API methods
    def set_status(self, connected: bool):
        self.is_connected = connected
        self.display_card.set_status(connected)

    def set_languages_from_codes(self, source_code: str, target_code: str) -> None:
        self._source_lang = self.LANG_CODE_TO_LABEL.get(source_code, "Korean")
        self._target_lang = self.LANG_CODE_TO_LABEL.get(target_code, "English")
        self.language_card.set_languages(self._source_lang, self._target_lang)

    def set_translation_enabled(self, enabled: bool) -> None:
        self.is_translation_on = bool(enabled)
        self.trans_button.set_state(self.is_translation_on)

    def set_stt_enabled(self, enabled: bool) -> None:
        self.is_stt_on = bool(enabled)
        self.stt_button.set_state(self.is_stt_on)

    def set_translation_needs_key(self, needs_key: bool) -> None:
        self.translation_needs_key = bool(needs_key)
        if needs_key and not self.is_translation_on:
            self.trans_button.set_state(False, needs_key=True)

    def set_stt_needs_key(self, needs_key: bool) -> None:
        self.stt_needs_key = bool(needs_key)
        if needs_key and not self.is_stt_on:
            self.stt_button.set_state(False, needs_key=True)

    def set_display_text(self, text: str, is_error: bool = False) -> None:
        """Update the display card with new text (STT result, translation, or error)."""
        self.display_card.set_display(text, is_error=is_error)
