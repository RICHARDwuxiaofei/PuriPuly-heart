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

# CJK (Chinese, Japanese, Korean) characters start at this Unicode point.
_CJK_START = 0x3000


def _weighted_len(text: str) -> int:
    """Calculate weighted length for CJK-aware font sizing."""
    return sum(2 if ord(char) >= _CJK_START else 1 for char in text)


def _display_size_for_length(length: int) -> int:
    if length <= 12:
        return 48
    if length <= 20:
        return 40
    if length <= 32:
        return 34
    if length <= 44:
        return 28
    return 24


class DisplayCard(ft.Container):
    """Multi-purpose display card with input field and decorative gradient."""

    def __init__(self, on_submit: Callable[[str], None]):
        self._on_submit = on_submit
        self._is_connected = False
        self._showing_status = True
        self._primary_value = t("display.disconnected")
        self._secondary_value: str | None = None
        self._primary_font_family: str | None = None
        self._secondary_font_family: str | None = None

        self._display_primary = ft.Text(
            self._primary_value,
            size=48,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self._display_secondary = ft.Text(
            "",
            size=48,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
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
                    content=ft.Column(
                        [
                            self._display_primary,
                            self._display_secondary,
                        ],
                        spacing=4,
                    ),
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
        """Update the primary display text and clear any secondary text."""
        self._showing_status = False
        self._primary_value = text
        self._primary_font_family = font_family
        self._secondary_value = None
        self._secondary_font_family = None
        self._sync_display(is_error=is_error)

    def set_display_translation(self, text: str | None, font_family: str | None = None) -> None:
        """Update the secondary display text and sync font sizing."""
        self._showing_status = False
        self._secondary_value = text or None
        self._secondary_font_family = font_family if text else None
        self._sync_display()

    def set_status(self, connected: bool, font_family: str | None = None):
        """Update connection status display."""
        self._is_connected = connected
        self._showing_status = True
        text = t("display.connected") if connected else t("display.disconnected")

        self._primary_value = text
        self._primary_font_family = font_family
        self._secondary_value = None
        self._secondary_font_family = None
        self._sync_display()

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
            self._primary_value = (
                t("display.connected") if self._is_connected else t("display.disconnected")
            )
            self._primary_font_family = display_font_family
            self._secondary_value = None
            self._secondary_font_family = None
            self._sync_display()

    def _sync_display(self, *, is_error: bool = False) -> None:
        primary_text = self._primary_value or ""
        secondary_text = self._secondary_value or ""
        max_len = max(_weighted_len(primary_text), _weighted_len(secondary_text))
        new_size = _display_size_for_length(max_len)

        text_color = COLOR_NEUTRAL_DARK if not is_error else COLOR_NEUTRAL_DARK

        self._display_primary.value = primary_text
        self._display_primary.size = new_size
        self._display_primary.color = text_color
        self._display_primary.font_family = self._primary_font_family

        self._display_secondary.value = secondary_text
        self._display_secondary.visible = bool(self._secondary_value)
        self._display_secondary.size = new_size
        self._display_secondary.color = text_color
        self._display_secondary.font_family = self._secondary_font_family

        if self._display_primary.page is not None:
            self._display_primary.update()
        if self._display_secondary.page is not None:
            self._display_secondary.update()
