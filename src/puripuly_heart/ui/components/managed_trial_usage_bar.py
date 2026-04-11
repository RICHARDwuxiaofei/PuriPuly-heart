from __future__ import annotations

import flet as ft

from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE,
    COLOR_SURFACE_TONAL,
)

_FIELD_HEIGHT = 72
_TRACK_RADIUS = 12
_TEXT_SIZE = 18
_TEXT_HORIZONTAL_PADDING = 16
_STATUS_ICON_SIZE = 36


class ManagedTrialUsageBar(ft.Row):
    def __init__(self, percent: int | None = None) -> None:
        self._percent: int | None = None
        self._fill_segment = ft.Container(
            height=_FIELD_HEIGHT,
            bgcolor=COLOR_PRIMARY_CONTAINER,
            border_radius=_TRACK_RADIUS,
        )
        self._empty_segment = ft.Container(
            height=_FIELD_HEIGHT,
            bgcolor=ft.Colors.TRANSPARENT,
        )
        self._fill_segments = ft.Row(
            controls=[self._empty_segment],
            spacing=0,
            expand=True,
        )
        self._remaining_text = ft.Text(
            "",
            size=_TEXT_SIZE,
            weight=ft.FontWeight.BOLD,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.RIGHT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._track = ft.Container(
            expand=True,
            height=_FIELD_HEIGHT,
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=_TRACK_RADIUS,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Stack(
                controls=[
                    ft.Container(
                        content=self._fill_segments,
                        bgcolor=COLOR_SURFACE_TONAL,
                        left=0,
                        right=0,
                        top=0,
                        bottom=0,
                    ),
                    ft.Container(
                        content=self._remaining_text,
                        alignment=ft.alignment.center_right,
                        padding=ft.padding.symmetric(horizontal=_TEXT_HORIZONTAL_PADDING),
                        left=0,
                        right=0,
                        top=0,
                        bottom=0,
                    ),
                ],
                expand=True,
            ),
        )
        self._status_icon = ft.Icon(size=_STATUS_ICON_SIZE)
        super().__init__(
            controls=[self._track, self._status_icon],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.set_percent(percent)

    @property
    def percent(self) -> int | None:
        return self._percent

    def _sync_fill_segments(self) -> None:
        if self._percent is None or self._percent <= 0:
            self._empty_segment.expand = 1
            self._fill_segments.controls = [self._empty_segment]
            return

        if self._percent >= 100:
            self._fill_segment.expand = 1
            self._fill_segments.controls = [self._fill_segment]
            return

        self._fill_segment.expand = self._percent
        self._empty_segment.expand = 100 - self._percent
        self._fill_segments.controls = [self._fill_segment, self._empty_segment]

    def set_percent(self, percent: int | None) -> None:
        if percent is None:
            self._percent = None
        else:
            self._percent = max(0, min(100, int(percent)))
        self._sync()

    def apply_locale(self) -> None:
        self._sync()

    def _sync(self) -> None:
        self._sync_fill_segments()
        if self._percent is None:
            self._remaining_text.value = t("settings.managed_trial_usage.remaining_placeholder")
            self._status_icon.name = ft.Icons.HOURGLASS_TOP_ROUNDED
            self._status_icon.color = COLOR_NEUTRAL
            self._status_icon.tooltip = t("api_key.status.verifying")
        else:
            self._remaining_text.value = t(
                "settings.managed_trial_usage.remaining",
                percent=self._percent,
            )
            self._status_icon.name = ft.Icons.CHECK_CIRCLE_ROUNDED
            self._status_icon.color = COLOR_PRIMARY
            self._status_icon.tooltip = t("api_key.status.success")
