"""Audio settings component with Host API, Microphone, and VAD."""

from __future__ import annotations

import logging
from typing import Callable

import flet as ft

from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_NEUTRAL, COLOR_NEUTRAL_DARK, COLOR_PRIMARY

logger = logging.getLogger(__name__)


class AudioSettings(ft.Column):
    """Audio settings: Host API, Microphone, and VAD sensitivity."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
    ):
        self._on_change = on_change
        self._default_option_label = t("settings.default_option")

        self._host_api_dropdown = ft.Dropdown(
            label=t("settings.audio_host_api"),
            options=[ft.dropdown.Option(self._default_option_label)],
            on_change=self._handle_host_api_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )

        self._microphone_dropdown = ft.Dropdown(
            label=t("settings.microphone"),
            options=[ft.dropdown.Option(self._default_option_label)],
            on_change=self._handle_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )

        self._vad_label = ft.Text(
            t("settings.vad_sensitivity"),
            size=20,
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
            on_change=self._handle_slider_visual_change,
            on_change_end=self._handle_change,
        )

        self._populate_host_apis()

        super().__init__(
            controls=[
                self._host_api_dropdown,
                self._microphone_dropdown,
                self._vad_label,
                self._vad_slider,
            ],
            spacing=16,
        )

    @property
    def host_api(self) -> str:
        """Get selected host API (empty string for default)."""
        val = self._host_api_dropdown.value or self._default_option_label
        return "" if val == self._default_option_label else val

    @host_api.setter
    def host_api(self, val: str) -> None:
        self._host_api_dropdown.value = val or self._default_option_label
        self._refresh_microphones()
        if self._host_api_dropdown.page:
            self._host_api_dropdown.update()

    @property
    def microphone(self) -> str:
        """Get selected microphone (empty string for default)."""
        val = self._microphone_dropdown.value or self._default_option_label
        return "" if val == self._default_option_label else val

    @microphone.setter
    def microphone(self, val: str) -> None:
        self._microphone_dropdown.value = val or self._default_option_label
        if self._microphone_dropdown.page:
            self._microphone_dropdown.update()

    @property
    def vad_sensitivity(self) -> float:
        """Get VAD sensitivity value."""
        return float(self._vad_slider.value or 0.5)

    @vad_sensitivity.setter
    def vad_sensitivity(self, val: float) -> None:
        self._vad_slider.value = val
        self._vad_slider.label = f"{val:.2f}"
        if self._vad_slider.page:
            self._vad_slider.update()

    def _populate_host_apis(self) -> None:
        """Populate host API dropdown with available APIs."""
        options = [ft.dropdown.Option(self._default_option_label)]
        allowed_apis = {"windows directsound", "windows wasapi"}

        try:
            import sounddevice as sd

            for api in sd.query_hostapis():
                name = str(api.get("name", "") or "").strip()
                if name and name.lower() in allowed_apis:
                    options.append(ft.dropdown.Option(name))
        except Exception as e:
            logger.warning(f"Failed to enumerate host APIs: {e}")

        self._host_api_dropdown.options = options

    def _refresh_microphones(self) -> None:
        """Refresh microphone list based on selected host API."""
        host_api = self.host_api
        devices = [self._default_option_label]

        try:
            import sounddevice as sd

            hostapi_index: int | None = None
            if host_api:
                for idx, item in enumerate(sd.query_hostapis()):
                    name = str(item.get("name", "") or "")
                    if name == host_api:
                        hostapi_index = idx
                        break

            for dev in sd.query_devices():
                if int(dev.get("max_input_channels", 0) or 0) <= 0:
                    continue
                if hostapi_index is not None and int(dev.get("hostapi", -1) or -1) != hostapi_index:
                    continue
                name = str(dev.get("name", "") or "").strip()
                if name:
                    devices.append(name)
        except Exception as e:
            logger.warning(f"Failed to enumerate microphones: {e}")

        current = self._microphone_dropdown.value
        self._microphone_dropdown.options = [ft.dropdown.Option(d) for d in devices]
        if current in devices:
            self._microphone_dropdown.value = current
        else:
            self._microphone_dropdown.value = self._default_option_label

        if self._microphone_dropdown.page:
            self._microphone_dropdown.update()

    def _handle_host_api_change(self, e) -> None:
        self._refresh_microphones()
        self._emit_change()

    def _handle_slider_visual_change(self, e) -> None:
        self._vad_slider.label = f"{float(e.control.value):.2f}"
        self._vad_slider.update()

    def _handle_change(self, e) -> None:
        self._emit_change()

    def _emit_change(self) -> None:
        if self._on_change:
            self._on_change()

    def apply_locale(self) -> None:
        """Update labels when locale changes."""
        old_default = self._default_option_label
        self._default_option_label = t("settings.default_option")

        self._host_api_dropdown.label = t("settings.audio_host_api")
        self._microphone_dropdown.label = t("settings.microphone")
        self._vad_label.value = t("settings.vad_sensitivity")

        # Update default option labels
        if self._host_api_dropdown.value == old_default:
            self._host_api_dropdown.value = self._default_option_label
        if self._microphone_dropdown.value == old_default:
            self._microphone_dropdown.value = self._default_option_label

        self._populate_host_apis()
        self._refresh_microphones()

        if self.page:
            self.update()
