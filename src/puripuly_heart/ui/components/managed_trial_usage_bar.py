from __future__ import annotations

import flet as ft

from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import COLOR_DIVIDER, COLOR_ON_BACKGROUND, COLOR_PRIMARY

_BAR_HEIGHT = 160
_BAR_WIDTH = 40


class ManagedTrialUsageBar(ft.Column):
    def __init__(self, percent: int | None = None) -> None:
        self._percent: int | None = None
        self._fill = ft.Container(
            width=_BAR_WIDTH,
            height=0,
            bgcolor=COLOR_PRIMARY,
            border_radius=999,
        )
        self._track = ft.Container(
            width=_BAR_WIDTH,
            height=_BAR_HEIGHT,
            bgcolor=ft.Colors.with_opacity(0.18, COLOR_DIVIDER),
            border_radius=999,
            alignment=ft.alignment.bottom_center,
            content=self._fill,
        )
        self._remaining_text = ft.Text(
            "",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        super().__init__(
            controls=[self._track, self._remaining_text],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.set_percent(percent)

    @property
    def percent(self) -> int | None:
        return self._percent

    def _fill_height_for_percent(self, percent: int) -> int:
        return round(_BAR_HEIGHT * percent / 100)

    def set_percent(self, percent: int | None) -> None:
        if percent is None:
            self._percent = None
        else:
            self._percent = max(0, min(100, int(percent)))
        self._sync()

    def apply_locale(self) -> None:
        self._sync()

    def _sync(self) -> None:
        if self._percent is None:
            self._fill.height = 0
            self._remaining_text.value = t("settings.managed_trial_usage.remaining_placeholder")
        else:
            self._fill.height = self._fill_height_for_percent(self._percent)
            self._remaining_text.value = t(
                "settings.managed_trial_usage.remaining",
                percent=self._percent,
            )
