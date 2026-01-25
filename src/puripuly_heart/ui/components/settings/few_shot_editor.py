from __future__ import annotations

import json
from typing import Callable

import flet as ft

from puripuly_heart.config.prompts import load_qwen_few_shot
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_ERROR,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
)


class FewShotEditor(ft.Column):
    """Qwen Few-Shot examples editor component using JSON text field."""

    def __init__(
        self,
        on_change: Callable[[list[dict[str, str]]], None] | None = None,
    ):
        self._on_change = on_change
        self._current_value: list[dict[str, str]] = []

        # --- Header ---
        self._title = ft.Text(
            t("settings.few_shot_header"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )

        self._reset_btn = ft.TextButton(
            text=t("settings.reset_examples"),
            icon=ft.Icons.REFRESH_ROUNDED,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                icon_color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._on_reset_click,
        )

        self._header = ft.Row(
            controls=[self._title, ft.Container(expand=True), self._reset_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Editor ---
        self._text_field = ft.TextField(
            multiline=True,
            min_lines=10,
            on_change=self._handle_change,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            text_size=16,
            color=COLOR_NEUTRAL_DARK,
            hint_text='[{"source": "...", "target": "..."}]',
        )

        self._error_text = ft.Text(
            "",
            color=COLOR_ERROR,
            size=12,
            visible=False,
        )

        super().__init__(
            controls=[self._header, self._text_field, self._error_text],
            spacing=8,
        )

    @property
    def value(self) -> list[dict[str, str]]:
        """Get current few-shot list."""
        return self._current_value

    @value.setter
    def value(self, val: list[dict[str, str]]) -> None:
        """Set few-shot list."""
        self._current_value = val
        self._text_field.value = json.dumps(val, ensure_ascii=False, indent=2)
        self._text_field.border_color = COLOR_DIVIDER
        self._error_text.visible = False
        if self._text_field.page:
            self._text_field.update()
            self._error_text.update()

    def load_defaults(self) -> None:
        """Load default few-shot examples from file."""
        defaults = load_qwen_few_shot()
        self.value = defaults
        self._emit_change()

    def apply_locale(self) -> None:
        """Update localized text."""
        from puripuly_heart.ui.fonts import font_for_language
        from puripuly_heart.ui.i18n import get_locale

        self._title.value = t("settings.few_shot_header")
        self._reset_btn.text = t("settings.reset_examples")

        # Update button style with current font
        font_family = font_for_language(get_locale())
        self._reset_btn.style = ft.ButtonStyle(
            color={
                ft.ControlState.HOVERED: COLOR_PRIMARY,
                ft.ControlState.DEFAULT: COLOR_NEUTRAL,
            },
            icon_color={
                ft.ControlState.HOVERED: COLOR_PRIMARY,
                ft.ControlState.DEFAULT: COLOR_NEUTRAL,
            },
            text_style={
                ft.ControlState.HOVERED: ft.TextStyle(size=20, font_family=font_family),
                ft.ControlState.DEFAULT: ft.TextStyle(size=20, font_family=font_family),
            },
            overlay_color=ft.Colors.TRANSPARENT,
            animation_duration=0,
        )

        if self.page:
            self._title.update()
            self._reset_btn.update()

    def _on_reset_click(self, e) -> None:
        self.load_defaults()

    def _handle_change(self, e) -> None:
        raw = self._text_field.value
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("Root must be a list")

            valid_list: list[dict[str, str]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError("Items must be objects")
                if "source" not in item or "target" not in item:
                    raise ValueError("Items must have 'source' and 'target'")
                valid_list.append({"source": str(item["source"]), "target": str(item["target"])})

            self._current_value = valid_list
            self._text_field.border_color = COLOR_PRIMARY
            self._error_text.visible = False
            self._emit_change()

        except json.JSONDecodeError as exc:
            self._text_field.border_color = COLOR_ERROR
            self._error_text.value = f"Invalid JSON: {exc.msg}"
            self._error_text.visible = True
        except ValueError as exc:
            self._text_field.border_color = COLOR_ERROR
            self._error_text.value = str(exc)
            self._error_text.visible = True

        self.update()

    def _emit_change(self) -> None:
        if self._on_change:
            self._on_change(self.value)
