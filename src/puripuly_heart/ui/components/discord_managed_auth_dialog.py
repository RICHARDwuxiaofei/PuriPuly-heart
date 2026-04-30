from __future__ import annotations

from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE,
    COLOR_SURFACE_TONAL,
    get_card_shadow,
)


class DiscordManagedAuthDialog:
    action_labels = [
        "discord_auth.continue",
        "discord_auth.byok",
        "discord_auth.close",
    ]
    waiting_action_labels = [
        "discord_auth.reopen_browser",
        "discord_auth.cancel",
    ]

    def __init__(
        self,
        page: ft.Page,
        *,
        on_continue: Callable[[], None],
        on_byok: Callable[[], None],
        on_close: Callable[[], None],
        on_reopen_browser: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._page = page
        self._on_continue = on_continue
        self._on_byok = on_byok
        self._on_close = on_close
        self._on_reopen_browser = on_reopen_browser
        self._on_cancel = on_cancel
        self._dialog: ft.AlertDialog | None = None
        self._is_open = False
        self._is_waiting = False

        self._title_text: ft.Text | None = None
        self._body_text: ft.Text | None = None
        self._requirements_text: ft.Text | None = None
        self._requirements_card_container: ft.Container | None = None
        self._actions: ft.Row | None = None
        self._continue_button: ft.ElevatedButton | None = None
        self._byok_button: ft.TextButton | None = None
        self._close_button: ft.TextButton | None = None
        self._reopen_browser_button: ft.ElevatedButton | None = None
        self._cancel_button: ft.TextButton | None = None

    def open(self) -> None:
        if self._dialog is not None and self._is_open:
            return

        self._is_waiting = False
        self._title_text = ft.Text(
            t("discord_auth.title"),
            size=26,
            color=COLOR_ON_BACKGROUND,
            weight=ft.FontWeight.BOLD,
        )
        self._body_text = ft.Text(
            t("discord_auth.body"),
            size=17,
            color=COLOR_ON_BACKGROUND,
        )
        self._requirements_text = ft.Text(
            t("discord_auth.requirements"),
            size=15,
            color=COLOR_ON_BACKGROUND,
        )
        self._actions = ft.Row(
            controls=self._build_initial_actions(),
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            wrap=True,
        )

        modal_content = ft.Container(
            width=660,
            padding=ft.padding.symmetric(horizontal=32, vertical=30),
            bgcolor=COLOR_SURFACE,
            border_radius=30,
            border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
            shadow=get_card_shadow(),
            content=ft.Column(
                controls=[
                    self._accent_bar(),
                    ft.Container(height=12),
                    self._title_text,
                    ft.Container(height=10),
                    ft.Column(
                        controls=[
                            self._body_text,
                            self._requirements_card(),
                        ],
                        spacing=14,
                        tight=True,
                    ),
                    ft.Container(height=18),
                    ft.Container(
                        content=self._actions,
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
        self._is_open = True

    def set_waiting(self) -> None:
        self._is_waiting = True
        if self._title_text is None or self._body_text is None or self._actions is None:
            return

        self._title_text.value = t("discord_auth.waiting_title")
        self._body_text.value = t("discord_auth.waiting_body")
        if self._requirements_text is not None:
            self._requirements_text.visible = False
        if self._requirements_card_container is not None:
            self._requirements_card_container.visible = False
        self._actions.controls = self._build_waiting_actions()
        self._update_page_if_possible()

    def close(self) -> None:
        if self._dialog is None or not self._is_open:
            return
        self._page.close(self._dialog)
        self._is_open = False

    def _accent_bar(self) -> ft.Container:
        return ft.Container(
            width=70,
            height=8,
            bgcolor=COLOR_PRIMARY_CONTAINER,
            border_radius=999,
        )

    def _requirements_card(self) -> ft.Container:
        self._requirements_card_container = ft.Container(
            content=self._requirements_text,
            padding=ft.padding.symmetric(horizontal=16, vertical=14),
            bgcolor=COLOR_SURFACE_TONAL,
            border=ft.border.all(1, ft.Colors.with_opacity(0.45, COLOR_DIVIDER)),
            border_radius=20,
        )
        return self._requirements_card_container

    def _build_initial_actions(self) -> list[ft.Control]:
        self._continue_button = ft.ElevatedButton(
            text=t("discord_auth.continue"),
            on_click=lambda _: self._on_continue(),
            style=ft.ButtonStyle(
                bgcolor=COLOR_PRIMARY,
                color=ft.Colors.WHITE,
                padding=ft.padding.symmetric(horizontal=24, vertical=18),
                shape=ft.RoundedRectangleBorder(radius=18),
            ),
        )
        self._byok_button = ft.TextButton(
            text=t("discord_auth.byok"),
            on_click=lambda _: self._close_then(self._on_byok),
            style=ft.ButtonStyle(
                color=COLOR_ON_BACKGROUND,
                padding=ft.padding.symmetric(horizontal=14, vertical=16),
                shape=ft.RoundedRectangleBorder(radius=18),
                overlay_color=ft.Colors.TRANSPARENT,
            ),
        )
        self._close_button = ft.TextButton(
            text=t("discord_auth.close"),
            on_click=lambda _: self._close_then(self._on_close),
            style=ft.ButtonStyle(
                color=COLOR_NEUTRAL,
                padding=ft.padding.symmetric(horizontal=14, vertical=16),
                shape=ft.RoundedRectangleBorder(radius=18),
                overlay_color=ft.Colors.TRANSPARENT,
            ),
        )
        return [self._continue_button, self._byok_button, self._close_button]

    def _build_waiting_actions(self) -> list[ft.Control]:
        controls: list[ft.Control] = []
        self._reopen_browser_button = None
        if self._on_reopen_browser is not None:
            self._reopen_browser_button = ft.ElevatedButton(
                text=t("discord_auth.reopen_browser"),
                on_click=lambda _: self._reopen_browser(),
                style=ft.ButtonStyle(
                    bgcolor=COLOR_PRIMARY,
                    color=ft.Colors.WHITE,
                    padding=ft.padding.symmetric(horizontal=22, vertical=16),
                    shape=ft.RoundedRectangleBorder(radius=18),
                ),
            )
            controls.append(self._reopen_browser_button)
        self._cancel_button = ft.TextButton(
            text=t("discord_auth.cancel"),
            on_click=lambda _: self._cancel_waiting(),
            style=ft.ButtonStyle(
                color=COLOR_ON_BACKGROUND,
                padding=ft.padding.symmetric(horizontal=14, vertical=16),
                shape=ft.RoundedRectangleBorder(radius=18),
                overlay_color=ft.Colors.TRANSPARENT,
            ),
        )
        controls.append(self._cancel_button)
        return controls

    def _close_then(self, action: Callable[[], None]) -> None:
        self.close()
        action()

    def _reopen_browser(self) -> None:
        if self._on_reopen_browser is not None:
            self._on_reopen_browser()

    def _cancel_waiting(self) -> None:
        self.close()
        if self._on_cancel is not None:
            self._on_cancel()
        else:
            self._on_close()

    def _update_page_if_possible(self) -> None:
        update = getattr(self._page, "update", None)
        if callable(update):
            update()
