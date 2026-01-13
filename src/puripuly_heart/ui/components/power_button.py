from typing import Callable

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    COLOR_TRANS_TONAL,
    COLOR_WARNING,
)


class PowerButton(ft.Container):
    """STT/TRANS toggle button with ON/OFF/Warning states."""

    def __init__(
        self,
        label: str,
        icon: str,
        on_click: Callable[[], None],
        icon_size: int = 80,
        label_size: int = 32,
    ):
        self._label = label
        self._icon = icon
        self._on_click = on_click
        self._is_on = False
        self._needs_key = False

        self._icon_control = ft.Icon(name=icon, size=icon_size, color=COLOR_SECONDARY)
        self._label_control = ft.Text(
            label,
            size=label_size,
            weight=ft.FontWeight.BOLD,
            color=COLOR_SECONDARY,
        )

        super().__init__(
            content=ft.Column(
                [self._icon_control, self._label_control],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=16,
            ),
            bgcolor=COLOR_TRANS_TONAL,
            border_radius=16,
            expand=True,
            alignment=ft.alignment.center,
            on_click=lambda _: self._on_click(),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            shadow=ft.BoxShadow(
                blur_radius=10,
                color=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
                spread_radius=0,
            ),
        )

    def set_state(self, is_on: bool, needs_key: bool = False):
        """Update button visual state."""
        self._is_on = is_on
        self._needs_key = needs_key

        if needs_key:
            self.bgcolor = COLOR_WARNING
            self._icon_control.color = ft.Colors.WHITE
            self._label_control.color = ft.Colors.WHITE
        elif is_on:
            self.bgcolor = COLOR_PRIMARY
            self._icon_control.color = ft.Colors.WHITE
            self._label_control.color = ft.Colors.WHITE
        else:
            self.bgcolor = COLOR_TRANS_TONAL
            self._icon_control.color = COLOR_SECONDARY
            self._label_control.color = COLOR_SECONDARY

        self.update()
