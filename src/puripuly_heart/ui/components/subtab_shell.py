from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Callable, Sequence

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_ON_BACKGROUND,
    COLOR_ON_PRIMARY_CONTAINER,
    COLOR_PRIMARY_CONTAINER,
    COLOR_SURFACE,
)


@dataclass(frozen=True)
class TextSubtab:
    key: str
    label: str
    controls: Sequence[ft.Control]


class _ScrollBody(ft.Column):
    def __init__(self, tab_key: str, controls: Sequence[ft.Control], *, on_scroll) -> None:
        super().__init__(
            controls=list(controls),
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            on_scroll=on_scroll,
            on_scroll_interval=0,
            visible=False,
        )
        self.tab_key = tab_key
        self.last_restore_offset = 0.0

    def restore_scroll(self, offset: float) -> None:
        self.last_restore_offset = offset
        if self.page is None:
            return
        with contextlib.suppress(Exception):
            self.scroll_to(offset=offset, duration=0)


class TextSubtabShell(ft.Column):
    def __init__(
        self,
        *,
        title: ft.Control,
        tabs: Sequence[TextSubtab],
        font_family: str | None = None,
        initial_key: str | None = None,
        on_tab_change: Callable[[str], None] | None = None,
    ) -> None:
        if not tabs:
            raise ValueError("TextSubtabShell requires at least one tab")

        self._font_family = font_family
        self._on_tab_change = on_tab_change
        self.tab_order = tuple(tab.key for tab in tabs)
        self.active_key = initial_key or self.tab_order[0]
        self.scroll_offsets = {tab.key: 0.0 for tab in tabs}

        self.title_region = ft.Container(content=title, padding=ft.padding.only(top=4, bottom=4))
        self.button_by_key = {tab.key: self._build_button(tab.key, tab.label) for tab in tabs}
        self.subtab_row = ft.Row(
            controls=[self.button_by_key[tab.key] for tab in tabs],
            spacing=8,
            wrap=False,
            scroll=ft.ScrollMode.AUTO,
        )
        self.subtab_bar = ft.Container(
            content=self.subtab_row,
            bgcolor=COLOR_SURFACE,
            border=ft.border.all(1, ft.Colors.with_opacity(0.8, COLOR_DIVIDER)),
            border_radius=24,
            padding=ft.padding.symmetric(horizontal=8, vertical=8),
        )
        self.body_by_key = {
            tab.key: _ScrollBody(
                tab.key,
                tab.controls,
                on_scroll=lambda e, tab_key=tab.key: self.record_scroll(tab_key, e),
            )
            for tab in tabs
        }
        self.body_host = ft.Stack(controls=list(self.body_by_key.values()), expand=True)

        super().__init__(
            controls=[self.title_region, self.subtab_bar, self.body_host],
            expand=True,
            spacing=16,
        )
        self._apply_button_states()
        self._set_visible_body(self.active_key)

    def _button_style(self, *, active: bool) -> ft.ButtonStyle:
        active_text = COLOR_ON_PRIMARY_CONTAINER if active else COLOR_ON_BACKGROUND
        active_bg = COLOR_PRIMARY_CONTAINER if active else ft.Colors.TRANSPARENT
        return ft.ButtonStyle(
            color={
                ft.ControlState.DEFAULT: active_text,
                ft.ControlState.HOVERED: active_text,
            },
            bgcolor={
                ft.ControlState.DEFAULT: active_bg,
                ft.ControlState.HOVERED: active_bg,
            },
            text_style=ft.TextStyle(
                size=16,
                weight=ft.FontWeight.W_600,
                font_family=self._font_family,
            ),
            overlay_color=ft.Colors.TRANSPARENT,
            padding=ft.padding.symmetric(horizontal=18, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=18),
            animation_duration=0,
        )

    def _build_button(self, key: str, label: str) -> ft.TextButton:
        return ft.TextButton(
            text=label,
            on_click=lambda _e, tab_key=key: self.select_tab(tab_key),
            style=self._button_style(active=key == self.active_key),
        )

    def _apply_button_states(self) -> None:
        for key, button in self.button_by_key.items():
            button.style = self._button_style(active=key == self.active_key)

    def _set_visible_body(self, key: str) -> None:
        for tab_key, body in self.body_by_key.items():
            body.visible = tab_key == key

    def set_font_family(self, font_family: str | None) -> None:
        self._font_family = font_family
        self._apply_button_states()

    def set_tab_label(self, key: str, label: str) -> None:
        self.button_by_key[key].text = label

    def select_tab(self, key: str) -> None:
        if key not in self.body_by_key or key == self.active_key:
            return
        self.active_key = key
        self._set_visible_body(key)
        self._apply_button_states()
        self.body_by_key[key].restore_scroll(self.scroll_offsets.get(key, 0.0))
        if self._on_tab_change is not None:
            self._on_tab_change(key)
        if self.page:
            self.update()

    def record_scroll(self, key: str, e) -> None:
        if key not in self.scroll_offsets:
            return
        self.scroll_offsets[key] = float(getattr(e, "pixels", 0.0) or 0.0)
