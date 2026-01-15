"""API key input field with verification button."""

from __future__ import annotations

import asyncio
from typing import Callable

import flet as ft
from flet import Colors as colors
from flet import Icons as icons

from puripuly_heart.ui.i18n import provider_label, t
from puripuly_heart.ui.theme import COLOR_NEUTRAL_DARK


class ApiKeyField(ft.Row):
    """API key input field with verification button and status indicator."""

    def __init__(
        self,
        label_key: str,
        secret_key: str,
        provider: str,
        on_verify: Callable[[str, str], object] | None = None,
        on_change: Callable[[str, str], None] | None = None,
    ):
        self._label_key = label_key
        self._secret_key = secret_key
        self._provider = provider
        self._on_verify = on_verify
        self._on_change = on_change

        self._text_field = ft.TextField(
            label=t(label_key),
            password=True,
            can_reveal_password=True,
            on_change=self._handle_change,
            border_radius=12,
            expand=True,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )

        self._verify_btn = ft.IconButton(
            icon=icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
            icon_color=colors.GREY_400,
            icon_size=28,
            tooltip=t("settings.verify_key"),
            on_click=self._handle_verify,
        )

        super().__init__(
            controls=[self._text_field, self._verify_btn],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    @property
    def value(self) -> str:
        """Get current field value."""
        return self._text_field.value or ""

    @value.setter
    def value(self, val: str) -> None:
        """Set field value."""
        self._text_field.value = val
        if self._text_field.page:
            self._text_field.update()

    def _handle_change(self, e) -> None:
        value = e.control.value or ""
        if self._on_change:
            self._on_change(self._secret_key, value)

    def _handle_verify(self, e) -> None:
        if not self._on_verify:
            return

        key = self.value
        if not key:
            if self.page:
                self.page.open(
                    ft.SnackBar(ft.Text(t("snackbar.api_key_empty")), bgcolor=colors.RED_400)
                )
            return

        async def _run():
            original_icon = self._verify_btn.icon
            original_color = self._verify_btn.icon_color

            self._verify_btn.icon = icons.HOURGLASS_TOP_ROUNDED
            self._verify_btn.icon_color = colors.BLUE_400
            if self._verify_btn.page:
                self._verify_btn.update()

            try:
                success, msg = await self._on_verify(self._provider, key)
                if success:
                    self.page.open(
                        ft.SnackBar(
                            ft.Text(
                                t(
                                    "snackbar.verification_ok",
                                    provider=provider_label(self._provider),
                                )
                            ),
                            bgcolor=colors.GREEN_400,
                        )
                    )
                    self._verify_btn.icon = icons.CHECK_CIRCLE_ROUNDED
                    self._verify_btn.icon_color = colors.GREEN_400
                else:
                    self.page.open(
                        ft.SnackBar(
                            ft.Text(t("snackbar.verification_failed", message=msg)),
                            bgcolor=colors.RED_400,
                        )
                    )
                    self._verify_btn.icon = icons.ERROR_OUTLINE_ROUNDED
                    self._verify_btn.icon_color = colors.RED_400
            except Exception as exc:
                self.page.open(
                    ft.SnackBar(
                        ft.Text(t("snackbar.verification_error", message=str(exc))),
                        bgcolor=colors.RED_400,
                    )
                )
                self._verify_btn.icon = icons.ERROR_OUTLINE_ROUNDED
                self._verify_btn.icon_color = colors.RED_400

            if self._verify_btn.page:
                self._verify_btn.update()

            await asyncio.sleep(3)

            self._verify_btn.icon = original_icon
            self._verify_btn.icon_color = original_color
            if self._verify_btn.page:
                self._verify_btn.update()

        self.page.run_task(_run)

    def apply_locale(self) -> None:
        """Update labels when locale changes."""
        self._text_field.label = t(self._label_key)
        self._verify_btn.tooltip = t("settings.verify_key")
        if self._text_field.page:
            self._text_field.update()
        if self._verify_btn.page:
            self._verify_btn.update()
