from __future__ import annotations

import re
from collections.abc import Callable, Sequence

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_ON_PRIMARY_CONTAINER,
    COLOR_PRIMARY,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE_TONAL,
)

_CHIP_TERM_WIDTH = 220
_CHIP_RADIUS = 999
_CHIP_HORIZONTAL_PADDING = 12
_ADD_CONTROL_HEIGHT = 40
_ADD_SPLIT_RE = re.compile(r"[,\r\n]+")


def _update_control_if_mounted(control: ft.Control) -> None:
    if getattr(control, "page", None) is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


class CustomVocabularyTagEditor(ft.Column):
    """Presentation component for editing Speech Recognition Hint chips."""

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

        self._empty_text = ft.Text(
            "",
            size=14,
            color=COLOR_NEUTRAL,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._chips_wrap = ft.Row(
            controls=[],
            spacing=8,
            run_spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._tag_area = ft.Column(
            controls=[self._chips_wrap, self._empty_text],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self._input_field = ft.TextField(
            hint_text="",
            multiline=True,
            min_lines=1,
            max_lines=2,
            dense=True,
            height=_ADD_CONTROL_HEIGHT,
            expand=True,
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            text_size=14,
            color=COLOR_NEUTRAL_DARK,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )
        self._add_button = ft.TextButton(
            text="",
            icon=ft.Icons.ADD_ROUNDED,
            height=_ADD_CONTROL_HEIGHT,
            on_click=self._handle_add_click,
            style=ft.ButtonStyle(
                color=COLOR_ON_PRIMARY_CONTAINER,
                bgcolor=COLOR_PRIMARY_CONTAINER,
                side=ft.BorderSide(1, COLOR_DIVIDER),
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.padding.symmetric(horizontal=14, vertical=8),
                icon_size=18,
            ),
        )
        self._add_row = ft.Row(
            controls=[self._input_field, self._add_button],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        super().__init__(
            controls=[self._tag_area, self._add_row],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.set_terms([])

    def set_terms(self, terms: list[str]) -> None:
        """Re-render hint chips and empty state from the provided terms."""
        self._terms = list(terms)
        self._chips_wrap.controls = [self._build_chip(term) for term in self._terms]
        self._empty_text.visible = not self._terms
        self._chips_wrap.visible = bool(self._terms)
        _update_control_if_mounted(self._chips_wrap)
        _update_control_if_mounted(self._empty_text)

    def set_placeholder(self, text: str) -> None:
        """Update add-input placeholder copy."""
        self._input_field.hint_text = text
        _update_control_if_mounted(self._input_field)

    def set_empty_text(self, text: str) -> None:
        """Update empty-state copy."""
        self._empty_text.value = text
        _update_control_if_mounted(self._empty_text)

    def set_remove_label_template(self, template: str) -> None:
        """Update remove tooltip/semantic copy for existing and future chips."""
        self._remove_label_template = template
        for chip in self._chips_wrap.controls:
            remove_button = self._chip_remove_button(chip)
            remove_button.tooltip = self._format_remove_label(str(chip.data))
            _update_control_if_mounted(remove_button)

    def set_add_label(self, text: str) -> None:
        """Update visible add control label."""
        self._add_button.text = text
        _update_control_if_mounted(self._add_button)

    def clear_input(self) -> None:
        """Clear unsubmitted add-input text."""
        self._input_field.value = ""
        _update_control_if_mounted(self._input_field)

    def _build_chip(self, term: str) -> ft.Container:
        term_text = ft.Text(
            term,
            size=14,
            color=COLOR_ON_BACKGROUND,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=True,
            width=_CHIP_TERM_WIDTH,
            tooltip=term,
            semantics_label=term,
        )
        remove_button = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_color=COLOR_NEUTRAL_DARK,
            icon_size=15,
            width=28,
            height=28,
            padding=0,
            visual_density=ft.VisualDensity.COMPACT,
            tooltip=self._format_remove_label(term),
            data=term,
            on_click=lambda _event, visible_term=term: self._handle_remove(visible_term),
        )
        return ft.Container(
            data=term,
            bgcolor=COLOR_SURFACE_TONAL,
            border=ft.border.all(1, COLOR_DIVIDER),
            border_radius=_CHIP_RADIUS,
            padding=ft.padding.only(
                left=_CHIP_HORIZONTAL_PADDING,
                right=4,
                top=4,
                bottom=4,
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Row(
                controls=[term_text, remove_button],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _format_remove_label(self, term: str) -> str | None:
        if not self._remove_label_template:
            return None
        return self._remove_label_template.format(term=term)

    def _chip_remove_button(self, chip: ft.Control) -> ft.IconButton:
        row = getattr(chip, "content", None)
        controls: Sequence[ft.Control] = getattr(row, "controls", ()) or ()
        return controls[1]  # type: ignore[return-value]

    def _handle_add_click(self, _event) -> None:
        raw_value = self._input_field.value or ""
        if raw_value == "":
            return

        raw_terms = [part for part in _ADD_SPLIT_RE.split(raw_value) if part != ""]
        self.clear_input()
        if raw_terms and self.on_add_terms is not None:
            self.on_add_terms(raw_terms)

    def _handle_remove(self, term: str) -> None:
        if self.on_remove_term is not None:
            self.on_remove_term(term)
