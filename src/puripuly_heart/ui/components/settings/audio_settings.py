"""Audio settings component with Host API and Microphone."""

from __future__ import annotations

import logging
from typing import Callable

import flet as ft

from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_ON_BACKGROUND, COLOR_PRIMARY

logger = logging.getLogger(__name__)


class AudioSettings(ft.Column):
    """Audio settings: Host API and Microphone (modal-based)."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
    ):
        self._on_change = on_change
        self._default_option_label = t("settings.default_option")

        # Current selections
        self._current_host_api = ""
        self._current_microphone = ""

        # Clickable text for Host API
        self._host_api_text = self._build_clickable_text(
            self._default_option_label,
            self._on_host_api_click,
        )

        # Clickable text for Microphone
        self._mic_text = self._build_clickable_text(
            self._default_option_label,
            self._on_mic_click,
        )

        super().__init__(
            controls=[
                self._host_api_text,
                ft.Container(height=8),
                self._mic_text,
            ],
            spacing=8,
            expand=True,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_clickable_text(self, text: str, on_click) -> ft.Container:
        """Build a clickable centered text with hover effect."""
        text_control = ft.Text(
            text,
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
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

    @property
    def host_api(self) -> str:
        """Get selected host API (empty string for default)."""
        return self._current_host_api

    @host_api.setter
    def host_api(self, val: str) -> None:
        self._current_host_api = val
        display = val or self._default_option_label
        self._host_api_text.content.value = display
        if self._host_api_text.page:
            self._host_api_text.update()

    @property
    def microphone(self) -> str:
        """Get selected microphone (empty string for default)."""
        return self._current_microphone

    @microphone.setter
    def microphone(self, val: str) -> None:
        self._current_microphone = val
        display = val or self._default_option_label
        self._mic_text.content.value = display
        if self._mic_text.page:
            self._mic_text.update()

    def _get_host_api_options(self) -> list[OptionItem]:
        """Get available host API options."""
        options = [OptionItem(value="", label=self._default_option_label)]
        allowed_apis = {"windows directsound", "windows wasapi"}

        try:
            import sounddevice as sd

            for api in sd.query_hostapis():
                name = str(api.get("name", "") or "").strip()
                if name and name.lower() in allowed_apis:
                    options.append(OptionItem(value=name, label=name))
        except Exception as e:
            logger.warning(f"Failed to enumerate host APIs: {e}")

        return options

    def _get_microphone_options(self) -> list[OptionItem]:
        """Get available microphone options based on selected host API."""
        options = [OptionItem(value="", label=self._default_option_label)]

        try:
            import sounddevice as sd

            hostapi_index: int | None = None
            if self._current_host_api:
                for idx, item in enumerate(sd.query_hostapis()):
                    name = str(item.get("name", "") or "")
                    if name == self._current_host_api:
                        hostapi_index = idx
                        break

            for dev in sd.query_devices():
                if int(dev.get("max_input_channels", 0) or 0) <= 0:
                    continue
                if hostapi_index is not None and int(dev.get("hostapi", -1) or -1) != hostapi_index:
                    continue
                name = str(dev.get("name", "") or "").strip()
                if name:
                    options.append(OptionItem(value=name, label=name))
        except Exception as e:
            logger.warning(f"Failed to enumerate microphones: {e}")

        return options

    def _on_host_api_click(self, e) -> None:
        """Open Host API selection modal."""
        if not self.page:
            return
        options = self._get_host_api_options()
        modal = SettingsModal(
            self.page,
            t("settings.audio_host_api"),
            options,
            self._on_host_api_selected,
            show_description=False,
        )
        modal.open(self._current_host_api)

    def _on_host_api_selected(self, value: str) -> None:
        """Handle host API selection from modal."""
        self.host_api = value
        # Reset microphone when host API changes
        self.microphone = ""
        self._emit_change()

    def _on_mic_click(self, e) -> None:
        """Open Microphone selection modal."""
        if not self.page:
            return
        options = self._get_microphone_options()
        modal = SettingsModal(
            self.page,
            t("settings.microphone"),
            options,
            self._on_mic_selected,
            show_description=False,
        )
        modal.open(self._current_microphone)

    def _on_mic_selected(self, value: str) -> None:
        """Handle microphone selection from modal."""
        self.microphone = value
        self._emit_change()

    def _emit_change(self) -> None:
        if self._on_change:
            self._on_change()

    def apply_locale(self) -> None:
        """Update labels when locale changes."""
        old_default = self._default_option_label
        self._default_option_label = t("settings.default_option")

        # Update display if showing default
        if self._host_api_text.content.value == old_default:
            self._host_api_text.content.value = self._default_option_label
        if self._mic_text.content.value == old_default:
            self._mic_text.content.value = self._default_option_label

        if self.page:
            self.update()
