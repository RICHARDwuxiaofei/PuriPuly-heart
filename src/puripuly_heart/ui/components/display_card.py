from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_SECONDARY,
    COLOR_SURFACE,
    get_card_shadow,
)


class DisplayCard(ft.Container):
    """Multi-purpose display card with input field and decorative gradient."""

    def __init__(self, on_submit: Callable[[str], None]):
        self._on_submit = on_submit
        self._is_connected = False
        self._showing_status = True

        self._display_text = ft.Text(
            t("display.disconnected"),
            size=48,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
        )

        self._input_field = ft.TextField(
            hint_text=t("display.input_hint"),
            border=ft.InputBorder.NONE,
            text_size=20,
            color=COLOR_NEUTRAL_DARK,
            hint_style=ft.TextStyle(color=COLOR_SECONDARY, italic=True),
            expand=True,
            on_submit=self._handle_submit,
        )

        main_content = ft.Column(
            [
                ft.Container(
                    content=self._display_text,
                    alignment=ft.alignment.top_left,
                    padding=ft.padding.only(left=8),
                ),
                ft.Column(
                    [
                        ft.Container(
                            content=ft.Divider(
                                height=1, color=ft.Colors.with_opacity(0.2, COLOR_NEUTRAL)
                            ),
                            padding=ft.padding.only(bottom=16),
                        ),
                        ft.Row(
                            [
                                ft.Container(
                                    content=ft.Text("•", size=36, color="#FFADAC"),
                                    padding=ft.padding.only(right=8),
                                ),
                                self._input_field,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    spacing=0,
                ),
            ],
            expand=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            spacing=8,
        )

        # Use the reusable glow stack wrapper
        # The content container handles the internal padding (32)
        content_with_glow = create_glow_stack(
            ft.Container(content=main_content, expand=True, padding=32)
        )

        super().__init__(
            content=content_with_glow,
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
        )

    def _handle_submit(self, e):
        text = e.control.value.strip()
        if text:
            self._on_submit(text)
            e.control.value = ""
            e.control.update()

    def set_display(self, text: str, is_error: bool = False, font_family: str | None = None):
        """Update the display text with dynamic sizing."""
        self._showing_status = False
        length = len(text)

        # Dynamic font sizing formula
        if length <= 15:
            new_size = 48
        elif length <= 30:
            new_size = 36
        elif length <= 50:
            new_size = 28
        elif length <= 100:
            new_size = 24
        else:
            new_size = 20

        self._display_text.value = text
        self._display_text.size = new_size
        self._display_text.color = COLOR_NEUTRAL_DARK
        self._display_text.font_family = font_family

        # Add safety for very long text
        self._display_text.max_lines = 6
        self._display_text.overflow = ft.TextOverflow.ELLIPSIS

        if self._display_text.page is not None:
            self._display_text.update()

    def set_status(self, connected: bool, font_family: str | None = None):
        """Update connection status display."""
        self._is_connected = connected
        self._showing_status = True
        text = t("display.connected") if connected else t("display.disconnected")

        # Reset to default big size for status
        self._display_text.value = text
        self._display_text.size = 48
        self._display_text.color = COLOR_NEUTRAL_DARK
        self._display_text.font_family = font_family
        self._display_text.max_lines = 1
        if self._display_text.page is not None:
            self._display_text.update()

    def clear_input(self):
        """Clear the input field."""
        self._input_field.value = ""
        self._input_field.update()

    def set_input_font(self, font_family: str | None) -> None:
        self._input_field.text_style = ft.TextStyle(font_family=font_family)
        self._input_field.hint_style = ft.TextStyle(
            color=COLOR_SECONDARY,
            italic=True,
            font_family=font_family,
        )
        if self._input_field.page is not None:
            self._input_field.update()

    def apply_locale(
        self,
        *,
        display_font_family: str | None = None,
        input_font_family: str | None = None,
    ) -> None:
        self._input_field.hint_text = t("display.input_hint")
        if input_font_family is not None:
            self.set_input_font(input_font_family)
        elif self._input_field.page is not None:
            self._input_field.update()
        if self._showing_status:
            self._display_text.value = (
                t("display.connected") if self._is_connected else t("display.disconnected")
            )
            self._display_text.font_family = display_font_family
            if self._display_text.page is not None:
                self._display_text.update()
