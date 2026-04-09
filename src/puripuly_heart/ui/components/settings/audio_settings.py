"""Audio settings component with Host API and Microphone."""

from __future__ import annotations

import logging
from typing import Callable

import flet as ft

from puripuly_heart.ui.components.settings.settings_modal import OptionItem, SettingsModal
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_ON_BACKGROUND, COLOR_PRIMARY

logger = logging.getLogger(__name__)
_CENTER_ALIGNMENT = ft.alignment.Alignment(0, 0)


class AudioSettings(ft.Column):
    """Audio settings for microphone and desktop loopback capture."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
    ):
        self._on_change = on_change
        self._default_option_label = t("settings.default_option")

        # Current selections
        self._current_host_api = ""
        self._current_microphone = ""
        self._current_desktop_output_device = ""
        self._current_desktop_vad_threshold = 0.6
        self._current_desktop_hangover_ms = 700
        self._current_desktop_pre_roll_ms = 500

        self._host_api_label = self._build_section_label(t("settings.audio_host_api"))
        self._microphone_label = self._build_section_label(t("settings.microphone"))
        self._desktop_output_label = self._build_section_label(
            t("settings.desktop_audio.output_device")
        )

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

        self._desktop_output_text = self._build_clickable_text(
            self._default_option_label,
            self._on_desktop_output_click,
        )

        self._desktop_vad_field = self._build_numeric_field(
            label=t("settings.desktop_audio.vad_speech_threshold"),
            value=f"{self._current_desktop_vad_threshold:.2f}",
            on_change_end=self._on_desktop_vad_threshold_change,
        )
        self._desktop_hangover_field = self._build_numeric_field(
            label=t("settings.desktop_audio.vad_hangover_ms"),
            value=str(self._current_desktop_hangover_ms),
            on_change_end=self._on_desktop_hangover_change,
        )
        self._desktop_pre_roll_field = self._build_numeric_field(
            label=t("settings.desktop_audio.vad_pre_roll_ms"),
            value=str(self._current_desktop_pre_roll_ms),
            on_change_end=self._on_desktop_pre_roll_change,
        )

        super().__init__(
            controls=[
                self._host_api_label,
                self._host_api_text,
                ft.Container(height=8),
                self._microphone_label,
                self._mic_text,
                ft.Container(height=12),
                self._desktop_output_label,
                self._desktop_output_text,
                ft.Container(height=12),
                ft.Row(
                    controls=[
                        self._desktop_vad_field,
                        self._desktop_hangover_field,
                        self._desktop_pre_roll_field,
                    ],
                    spacing=8,
                ),
            ],
            spacing=8,
            expand=True,
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _build_section_label(self, text: str) -> ft.Text:
        return ft.Text(text, size=15, color=COLOR_PRIMARY)

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
            alignment=_CENTER_ALIGNMENT,
            expand=True,
            on_click=on_click,
            on_hover=self._on_text_hover,
        )

    def _build_numeric_field(self, *, label: str, value: str, on_change_end) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value,
            dense=True,
            expand=True,
            text_align=ft.TextAlign.CENTER,
            on_blur=on_change_end,
            on_submit=on_change_end,
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

    @property
    def desktop_output_device(self) -> str:
        return self._current_desktop_output_device

    @desktop_output_device.setter
    def desktop_output_device(self, val: str) -> None:
        self._current_desktop_output_device = val
        display = val or self._default_option_label
        self._desktop_output_text.content.value = display
        if self._desktop_output_text.page:
            self._desktop_output_text.update()

    @property
    def desktop_vad_threshold(self) -> float:
        return self._current_desktop_vad_threshold

    @desktop_vad_threshold.setter
    def desktop_vad_threshold(self, val: float) -> None:
        self._current_desktop_vad_threshold = float(val)
        self._desktop_vad_field.value = f"{self._current_desktop_vad_threshold:.2f}"
        if self._desktop_vad_field.page:
            self._desktop_vad_field.update()

    @property
    def desktop_hangover_ms(self) -> int:
        return self._current_desktop_hangover_ms

    @desktop_hangover_ms.setter
    def desktop_hangover_ms(self, val: int) -> None:
        self._current_desktop_hangover_ms = int(val)
        self._desktop_hangover_field.value = str(self._current_desktop_hangover_ms)
        if self._desktop_hangover_field.page:
            self._desktop_hangover_field.update()

    @property
    def desktop_pre_roll_ms(self) -> int:
        return self._current_desktop_pre_roll_ms

    @desktop_pre_roll_ms.setter
    def desktop_pre_roll_ms(self, val: int) -> None:
        self._current_desktop_pre_roll_ms = int(val)
        self._desktop_pre_roll_field.value = str(self._current_desktop_pre_roll_ms)
        if self._desktop_pre_roll_field.page:
            self._desktop_pre_roll_field.update()

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

    def _get_desktop_output_options(self) -> list[OptionItem]:
        options = [OptionItem(value="", label=self._default_option_label)]

        manager = None
        try:
            import pyaudiowpatch as pyaudio  # type: ignore

            manager = pyaudio.PyAudio()
            seen: set[str] = set()
            for info in manager.get_loopback_device_info_generator():
                name = str(info.get("name", "") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                options.append(OptionItem(value=name, label=name))
        except Exception as e:
            logger.warning(f"Failed to enumerate desktop loopback outputs: {e}")
        finally:
            if manager is not None:
                try:
                    manager.terminate()
                except Exception:
                    pass

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

    def _on_desktop_output_click(self, e) -> None:
        """Open desktop loopback output selection modal."""
        if not self.page:
            return
        options = self._get_desktop_output_options()
        modal = SettingsModal(
            self.page,
            t("settings.desktop_audio.output_device"),
            options,
            self._on_desktop_output_selected,
            show_description=False,
        )
        modal.open(self._current_desktop_output_device)

    def _on_desktop_output_selected(self, value: str) -> None:
        self.desktop_output_device = value
        self._emit_change()

    def _on_desktop_vad_threshold_change(self, e) -> None:
        self.desktop_vad_threshold = self._parse_float(
            e.control.value,
            fallback=self._current_desktop_vad_threshold,
            minimum=0.0,
            maximum=1.0,
        )
        self._emit_change()

    def _on_desktop_hangover_change(self, e) -> None:
        self.desktop_hangover_ms = self._parse_int(
            e.control.value,
            fallback=self._current_desktop_hangover_ms,
            minimum=0,
        )
        self._emit_change()

    def _on_desktop_pre_roll_change(self, e) -> None:
        self.desktop_pre_roll_ms = self._parse_int(
            e.control.value,
            fallback=self._current_desktop_pre_roll_ms,
            minimum=0,
        )
        self._emit_change()

    def _parse_float(
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

    def _parse_int(
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

    def _emit_change(self) -> None:
        if self._on_change:
            self._on_change()

    def apply_locale(self) -> None:
        """Update labels when locale changes."""
        old_default = self._default_option_label
        self._default_option_label = t("settings.default_option")
        self._host_api_label.value = t("settings.audio_host_api")
        self._microphone_label.value = t("settings.microphone")
        self._desktop_output_label.value = t("settings.desktop_audio.output_device")
        self._desktop_vad_field.label = t("settings.desktop_audio.vad_speech_threshold")
        self._desktop_hangover_field.label = t("settings.desktop_audio.vad_hangover_ms")
        self._desktop_pre_roll_field.label = t("settings.desktop_audio.vad_pre_roll_ms")

        # Update display if showing default
        if self._host_api_text.content.value == old_default:
            self._host_api_text.content.value = self._default_option_label
        if self._mic_text.content.value == old_default:
            self._mic_text.content.value = self._default_option_label
        if self._desktop_output_text.content.value == old_default:
            self._desktop_output_text.content.value = self._default_option_label

        if self.page:
            self.update()
