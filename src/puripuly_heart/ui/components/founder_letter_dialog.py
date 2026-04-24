from __future__ import annotations

from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)


class FounderLetterDialog:
    def __init__(
        self,
        page: ft.Page,
        *,
        on_connect: Callable[[], None],
        on_contact: Callable[[], None],
    ) -> None:
        self._page = page
        self._on_connect = on_connect
        self._on_contact = on_contact
        self._dialog: ft.AlertDialog | None = None
        self._connect_button: ft.ElevatedButton | None = None
        self._contact_button: ft.TextButton | None = None

    def open(self) -> None:
        paragraphs = [
            t("openrouter.handoff.letter.p1"),
            t("openrouter.handoff.letter.p2"),
            t("openrouter.handoff.letter.p3"),
        ]
        body = ft.Column(
            controls=[
                ft.Text(
                    paragraph,
                    size=22 if index == 0 else 20,
                    color=COLOR_ON_BACKGROUND,
                    weight=ft.FontWeight.BOLD if index == 0 else ft.FontWeight.NORMAL,
                )
                for index, paragraph in enumerate(paragraphs)
            ],
            spacing=18,
            tight=True,
        )

        self._connect_button = ft.ElevatedButton(
            text=t("openrouter.handoff.connect"),
            on_click=lambda _: self._select(self._on_connect),
            style=ft.ButtonStyle(
                bgcolor=COLOR_PRIMARY,
                color=ft.Colors.WHITE,
                padding=ft.padding.symmetric(horizontal=24, vertical=18),
                shape=ft.RoundedRectangleBorder(radius=18),
            ),
        )
        self._contact_button = ft.TextButton(
            text=t("openrouter.handoff.contact"),
            on_click=lambda _: self._select(self._on_contact),
            style=ft.ButtonStyle(
                color=COLOR_ON_BACKGROUND,
                padding=ft.padding.symmetric(horizontal=14, vertical=16),
                shape=ft.RoundedRectangleBorder(radius=18),
                overlay_color=ft.Colors.TRANSPARENT,
            ),
        )

        actions = ft.Row(
            controls=[self._contact_button, self._connect_button],
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            wrap=True,
        )

        modal_content = ft.Container(
            width=720,
            padding=ft.padding.symmetric(horizontal=34, vertical=30),
            bgcolor=COLOR_SURFACE,
            border_radius=30,
            border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
            shadow=get_card_shadow(),
            content=ft.Column(
                controls=[
                    ft.Text(
                        t("openrouter.handoff.status"),
                        size=15,
                        color=COLOR_NEUTRAL,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Container(height=10),
                    body,
                    ft.Container(height=14),
                    ft.Container(
                        content=actions,
                        padding=ft.padding.only(top=4),
                        bgcolor=COLOR_BACKGROUND,
                        border_radius=22,
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

    def _select(self, action: Callable[[], None]) -> None:
        if self._dialog is not None:
            self._page.close(self._dialog)
        action()
