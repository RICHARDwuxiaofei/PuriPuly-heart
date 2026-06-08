from __future__ import annotations

import re
from collections.abc import Callable

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_PRIMARY_CONTAINER,
    COLOR_PRIMARY,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE_TONAL,
)

_CHIP_TERM_WIDTH = 220
_CHIP_COMPACT_CHAR_LIMIT = 24
_CHIP_RADIUS = 999
_CHIP_HORIZONTAL_PADDING = 16
_CHIP_VERTICAL_PADDING = 8
_TOKEN_INPUT_WIDTH = 180
_TOKEN_FIELD_RADIUS = 14
_TOKEN_FIELD_BORDER_WIDTH = 1
_TOKEN_FIELD_FOCUSED_BORDER_WIDTH = 1.5
_TOKEN_SPLIT_RE = re.compile(r"\s+")


def _update_control_if_mounted(control: ft.Control) -> None:
    if getattr(control, "page", None) is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


class CustomVocabularyTagEditor(ft.Column):
    """Presentation component for editing Speech Recognition Hint token chips."""

    def __init__(
        self,
        *,
        on_add_terms: Callable[[list[str]], None] | None = None,
        on_remove_term: Callable[[str], None] | None = None,
    ) -> None:
        self.on_add_terms = on_add_terms
        self.on_remove_term = on_remove_term
        self._terms: list[str] = []
        self._remove_label_template = ""
        self._add_label = ""

        self._empty_text = ft.Text(
            "",
            size=14,
            color=COLOR_NEUTRAL,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            visible=False,
        )
        self._chips_wrap = ft.Row(
            controls=[],
            spacing=6,
            run_spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self._input_field = ft.TextField(
            hint_text="",
            multiline=False,
            max_lines=1,
            dense=True,
            height=32,
            width=_TOKEN_INPUT_WIDTH,
            border=ft.InputBorder.NONE,
            border_width=0,
            focused_border_width=0,
            border_color=ft.Colors.TRANSPARENT,
            focused_border_color=ft.Colors.TRANSPARENT,
            bgcolor=ft.Colors.TRANSPARENT,
            focused_bgcolor=ft.Colors.TRANSPARENT,
            focused_color=COLOR_NEUTRAL_DARK,
            focus_color=ft.Colors.TRANSPARENT,
            text_size=14,
            color=COLOR_NEUTRAL_DARK,
            content_padding=ft.padding.symmetric(horizontal=2, vertical=6),
            on_change=self._handle_input_change,
            on_submit=self._handle_input_submit,
            on_focus=self._handle_input_focus,
            on_blur=self._handle_input_blur,
        )
        self._token_wrap = ft.Row(
            controls=[self._input_field],
            spacing=6,
            run_spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._token_field = ft.Container(
            content=self._token_wrap,
            width=float("inf"),
            bgcolor=COLOR_SURFACE_TONAL,
            border=self._token_field_border(focused=False),
            border_radius=_TOKEN_FIELD_RADIUS,
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            on_click=self._handle_token_field_click,
        )

        super().__init__(
            controls=[self._token_field],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.set_terms([])

    def set_terms(self, terms: list[str]) -> None:
        """Re-render hint chips and empty state from the provided terms."""
        self._terms = list(terms)
        chip_controls = [self._build_chip(term) for term in self._terms]
        self._chips_wrap.controls = chip_controls
        self._token_wrap.controls = [*chip_controls, self._input_field]
        self._empty_text.visible = False
        self._chips_wrap.visible = True
        _update_control_if_mounted(self._chips_wrap)
        _update_control_if_mounted(self._token_wrap)
        _update_control_if_mounted(self._empty_text)

    def set_placeholder(self, text: str) -> None:
        """Accept legacy placeholder copy; token input intentionally stays quiet."""
        _ = text
        self._input_field.hint_text = ""
        _update_control_if_mounted(self._input_field)

    def set_empty_text(self, text: str) -> None:
        """Update empty-state copy."""
        self._empty_text.value = text
        _update_control_if_mounted(self._empty_text)

    def set_remove_label_template(self, template: str) -> None:
        """Update remove tooltip/semantic copy for existing and future chips."""
        self._remove_label_template = template
        for chip in self._chips_wrap.controls:
            chip.tooltip = self._format_remove_label(str(chip.data))
            _update_control_if_mounted(chip)

    def set_add_label(self, text: str) -> None:
        """Accept legacy add-button copy; token input no longer renders a button."""
        self._add_label = text

    def clear_input(self) -> None:
        """Clear unsubmitted add-input text."""
        self._input_field.value = ""
        _update_control_if_mounted(self._input_field)

    def _term_text_width(self, term: str) -> int | None:
        if len(term) <= _CHIP_COMPACT_CHAR_LIMIT:
            return None
        return _CHIP_TERM_WIDTH

    def _build_chip(self, term: str) -> ft.Container:
        term_text = ft.Text(
            term,
            size=16,
            color=COLOR_ON_PRIMARY_CONTAINER,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=True,
            width=self._term_text_width(term),
            tooltip=term,
            semantics_label=term,
        )
        return ft.Container(
            data=term,
            tooltip=self._format_remove_label(term),
            bgcolor=COLOR_PRIMARY_CONTAINER,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=_CHIP_RADIUS,
            padding=ft.padding.only(
                left=_CHIP_HORIZONTAL_PADDING,
                right=_CHIP_HORIZONTAL_PADDING,
                top=_CHIP_VERTICAL_PADDING,
                bottom=_CHIP_VERTICAL_PADDING,
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=term_text,
            on_click=lambda _event, visible_term=term: self._handle_remove(visible_term),
        )

    def _format_remove_label(self, term: str) -> str | None:
        if not self._remove_label_template:
            return None
        return self._remove_label_template.format(term=term)

    def _token_field_border(self, *, focused: bool) -> ft.Border:
        return ft.border.all(
            _TOKEN_FIELD_FOCUSED_BORDER_WIDTH if focused else _TOKEN_FIELD_BORDER_WIDTH,
            COLOR_PRIMARY if focused else COLOR_DIVIDER,
        )

    def _token_field_focus_shadow(self) -> ft.BoxShadow:
        return ft.BoxShadow(
            blur_radius=8,
            color=ft.Colors.with_opacity(0.12, COLOR_PRIMARY),
            offset=ft.Offset(0, 0),
            spread_radius=0,
        )

    def _set_token_field_focused(self, focused: bool) -> None:
        self._token_field.border = self._token_field_border(focused=focused)
        self._token_field.shadow = self._token_field_focus_shadow() if focused else None
        _update_control_if_mounted(self._token_field)

    def _handle_token_field_click(self, _event) -> None:
        self._input_field.focus()

    def _handle_add_click(self, _event) -> None:
        self._commit_input_value()

    def _handle_input_change(self, _event) -> None:
        raw_value = self._input_field.value or ""
        if not raw_value or not _TOKEN_SPLIT_RE.search(raw_value):
            return
        self._commit_input_value()

    def _handle_input_submit(self, _event) -> None:
        self._commit_input_value()

    def _handle_input_focus(self, _event) -> None:
        self._set_token_field_focused(True)

    def _handle_input_blur(self, _event) -> None:
        self._set_token_field_focused(False)
        self._commit_input_value()

    def _commit_input_value(self) -> None:
        raw_value = self._input_field.value or ""
        if raw_value == "" or self.on_add_terms is None:
            return

        raw_terms = [part for part in _TOKEN_SPLIT_RE.split(raw_value.strip()) if part]
        self.clear_input()
        if raw_terms:
            self.on_add_terms(raw_terms)

    def _handle_remove(self, term: str) -> None:
        if self.on_remove_term is not None:
            self.on_remove_term(term)
