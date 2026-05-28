from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_PRIMARY,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE,
    get_card_shadow,
)

_DIALOG_WIDTH = 480
_METER_HEIGHT = 30
_METER_WIDTH = 360


def _clamp_level(value: float) -> float:
    level = max(0.0, min(1.0, float(value)))
    if level <= 1e-6:
        return 0.0
    return level


def _level_semantics_value(level: float) -> int:
    return int(round(_clamp_level(level) * 100))


class MicrophoneTestDialog:
    """Minimal microphone-test modal with a live accessible level meter."""

    def __init__(
        self,
        page: ft.Page,
        *,
        on_close: Callable[[], None],
    ) -> None:
        self._page = page
        self._on_close = on_close
        self._dialog: ft.AlertDialog | None = None
        self._level_bar: ft.ProgressBar | None = None
        self._level = 0.0
        self._is_open = False
        self._close_notified = False

    @property
    def dialog(self) -> ft.AlertDialog | None:
        return self._dialog

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        if self._is_open:
            return
        self._close_notified = False
        self._dialog = self._build_dialog()
        self._is_open = True
        self._page.open(self._dialog)

    def close(self, *, notify: bool = False) -> None:
        dialog = self._dialog
        if dialog is None:
            self._close_notified = True
            return
        was_open = self._is_open
        self._is_open = False
        if notify:
            self._notify_close_once()
        else:
            self._close_notified = True
        if was_open:
            close = getattr(self._page, "close", None)
            if callable(close):
                close(dialog)

    def set_level(self, value: float) -> None:
        self._level = _clamp_level(value)
        if self._level_bar is None:
            return
        self._sync_level_bar()
        if getattr(self._level_bar, "page", None) is None:
            return
        try:
            self._level_bar.update()
        except AssertionError as exc:
            if "Control must be added" not in str(exc):
                raise

    def _build_dialog(self) -> ft.AlertDialog:
        self._level_bar = ft.ProgressBar(
            value=self._level,
            semantics_label=t("settings.microphone_test.level_label"),
            semantics_value=_level_semantics_value(self._level),
            bar_height=_METER_HEIGHT,
            width=_METER_WIDTH,
            color=COLOR_PRIMARY,
            bgcolor=COLOR_PRIMARY_CONTAINER,
            border_radius=18,
        )

        title = ft.Text(
            t("settings.microphone_test"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
            font_family=font_for_language(get_locale()),
        )
        close_button = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_color=COLOR_NEUTRAL,
            tooltip=t("openrouter.handoff.close"),
            on_click=self._handle_close_click,
        )

        modal_content = ft.Container(
            width=_DIALOG_WIDTH,
            padding=ft.padding.symmetric(horizontal=32, vertical=28),
            bgcolor=COLOR_SURFACE,
            border_radius=28,
            border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
            shadow=get_card_shadow(),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[title, ft.Container(expand=True), close_button],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=self._level_bar,
                        alignment=ft.alignment.center,
                        padding=ft.padding.symmetric(horizontal=14, vertical=18),
                        bgcolor=COLOR_BACKGROUND,
                        border_radius=24,
                    ),
                ],
                spacing=24,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        return ft.AlertDialog(
            modal=True,
            content=create_glow_stack(modal_content),
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            surface_tint_color=ft.Colors.TRANSPARENT,
            semantics_label=t("settings.microphone_test"),
            on_dismiss=self._handle_dismiss,
        )

    def _sync_level_bar(self) -> None:
        if self._level_bar is None:
            return
        self._level_bar.value = self._level
        self._level_bar.semantics_value = _level_semantics_value(self._level)

    def _handle_close_click(self, _event) -> None:  # noqa: ANN001
        self.close(notify=True)

    def _handle_dismiss(self, _event) -> None:  # noqa: ANN001
        if not self._is_open:
            return
        self._is_open = False
        self._notify_close_once()

    def _notify_close_once(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        self._on_close()
