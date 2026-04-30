from __future__ import annotations

from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)


class PeerTranslationEulaDialog:
    def __init__(
        self,
        page: ft.Page,
        *,
        on_accept: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._page = page
        self._on_accept = on_accept
        self._on_cancel = on_cancel
        self._dialog: ft.AlertDialog | None = None

    def open(self) -> None:
        body = ft.Column(
            controls=[
                ft.Text(
                    t("peer_translation_eula.body"),
                    size=15,
                    color=COLOR_ON_BACKGROUND,
                ),
            ],
            spacing=12,
            tight=True,
        )
        actions = ft.Row(
            controls=[
                ft.TextButton(
                    text=t("peer_translation_eula.cancel"),
                    on_click=lambda _: self._select(self._on_cancel),
                ),
                ft.ElevatedButton(
                    text=t("peer_translation_eula.accept"),
                    on_click=lambda _: self._select(self._on_accept),
                    style=ft.ButtonStyle(
                        bgcolor=COLOR_PRIMARY,
                        color=ft.Colors.WHITE,
                        padding=ft.padding.symmetric(horizontal=22, vertical=16),
                        shape=ft.RoundedRectangleBorder(radius=16),
                    ),
                ),
            ],
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            wrap=True,
        )
        modal_content = ft.Container(
            width=640,
            padding=ft.padding.symmetric(horizontal=30, vertical=28),
            bgcolor=COLOR_SURFACE,
            border_radius=28,
            border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
            shadow=get_card_shadow(),
            content=ft.Column(
                controls=[
                    ft.Text(
                        t("peer_translation_eula.title"),
                        size=24,
                        color=COLOR_ON_BACKGROUND,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Container(height=8),
                    body,
                    ft.Container(height=10),
                    ft.Container(
                        content=actions,
                        padding=ft.padding.only(top=4),
                        bgcolor=COLOR_BACKGROUND,
                        border_radius=20,
                    ),
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )
        self._dialog = ft.AlertDialog(
            modal=True,
            content=create_glow_stack(modal_content),
            content_padding=0,
            bgcolor=ft.Colors.TRANSPARENT,
            surface_tint_color=ft.Colors.TRANSPARENT,
        )
        self._page.open(self._dialog)

    def _select(self, action: Callable[[], None] | None) -> None:
        if self._dialog is not None:
            self._page.close(self._dialog)
        if action is not None:
            action()
