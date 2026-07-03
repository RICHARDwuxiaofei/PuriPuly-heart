from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import get_locale, t
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)


class TelemetryConsentDialog:
    def __init__(
        self,
        page: ft.Page,
        *,
        on_allow: Callable[[], None],
        on_decline: Callable[[], None],
    ) -> None:
        self._page = page
        self._on_allow = on_allow
        self._on_decline = on_decline
        self._dialog: ft.AlertDialog | None = None

    def open(self) -> None:
        font_family = font_for_language(get_locale())
        content = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        t("telemetry.consent.title"),
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        color=COLOR_NEUTRAL,
                        font_family=font_family,
                    ),
                    ft.Text(
                        t("telemetry.consent.body"),
                        size=16,
                        color=COLOR_ON_BACKGROUND,
                        font_family=font_family,
                    ),
                    ft.Text(
                        t("telemetry.consent.privacy"),
                        size=15,
                        color=COLOR_NEUTRAL,
                        font_family=font_family,
                    ),
                    ft.Row(
                        [
                            ft.TextButton(
                                text=t("telemetry.consent.decline"),
                                on_click=lambda _e: self._choose(self._on_decline),
                            ),
                            ft.ElevatedButton(
                                text=t("telemetry.consent.allow"),
                                on_click=lambda _e: self._choose(self._on_allow),
                                bgcolor=COLOR_PRIMARY,
                                color=ft.Colors.WHITE,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                ],
                spacing=18,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=560,
            padding=ft.padding.symmetric(horizontal=32, vertical=32),
            bgcolor=COLOR_SURFACE,
            border_radius=28,
            shadow=get_card_shadow(),
        )
        self._dialog = ft.AlertDialog(
            modal=True,
            content=create_glow_stack(content),
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            surface_tint_color=ft.Colors.TRANSPARENT,
        )
        self._page.open(self._dialog)

    def _choose(self, callback: Callable[[], None]) -> None:
        if self._dialog is not None:
            self._page.close(self._dialog)
        callback()
