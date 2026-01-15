"""System prompt editor component."""

from __future__ import annotations

from typing import Callable

import flet as ft
from flet import Icons as icons

from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.ui.i18n import provider_label, t
from puripuly_heart.ui.theme import COLOR_NEUTRAL, COLOR_NEUTRAL_DARK


class PromptEditor(ft.Column):
    """System prompt editor with reset button."""

    def __init__(
        self,
        on_change: Callable[[str], None] | None = None,
    ):
        self._on_change = on_change
        self._current_provider = "gemini"

        self._provider_label = ft.Text(
            t("settings.prompt_for", provider=provider_label(self._current_provider)),
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )

        self._text_field = ft.TextField(
            label=t("settings.system_prompt"),
            multiline=True,
            min_lines=3,
            on_change=self._handle_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )

        self._reset_btn = ft.TextButton(
            text=t("settings.reset_prompt"),
            icon=icons.REFRESH_ROUNDED,
            style=ft.ButtonStyle(
                color=COLOR_NEUTRAL_DARK,
            ),
            on_click=self._handle_reset,
        )

        super().__init__(
            controls=[
                self._provider_label,
                self._text_field,
                ft.Row([self._reset_btn], alignment=ft.MainAxisAlignment.END),
            ],
            spacing=12,
        )

    @property
    def value(self) -> str:
        """Get current prompt value."""
        return self._text_field.value or ""

    @value.setter
    def value(self, val: str) -> None:
        """Set prompt value."""
        self._text_field.value = val
        if self._text_field.page:
            self._text_field.update()

    def set_provider(self, provider_name: str) -> None:
        """Update the current provider and reload prompt if empty."""
        self._current_provider = provider_name
        self._provider_label.value = t(
            "settings.prompt_for", provider=provider_label(provider_name)
        )
        if self._provider_label.page:
            self._provider_label.update()

    def load_default_prompt(self) -> None:
        """Load default prompt for current provider."""
        self.value = load_prompt_for_provider(self._current_provider)
        self._emit_change()

    def load_default_if_empty(self) -> None:
        """Load default prompt only if current value is empty."""
        if not self.value.strip():
            self.load_default_prompt()

    def _handle_change(self, e) -> None:
        self._emit_change()

    def _handle_reset(self, e) -> None:
        self.load_default_prompt()

    def _emit_change(self) -> None:
        if self._on_change:
            self._on_change(self.value)

    def apply_locale(self) -> None:
        """Update labels when locale changes."""
        self._provider_label.value = t(
            "settings.prompt_for", provider=provider_label(self._current_provider)
        )
        self._text_field.label = t("settings.system_prompt")
        self._reset_btn.text = t("settings.reset_prompt")
        if self.page:
            self.update()
