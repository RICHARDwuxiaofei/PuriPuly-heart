from typing import Callable

import flet as ft

from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL_DARK,
    COLOR_SURFACE,
    COLOR_SURFACE_TONAL,
    COLOR_TERTIARY,
)


class LanguageCard(ft.Container):
    """Language pair display card with clickable source/target areas."""

    def __init__(
        self,
        on_source_click: Callable[[], None],
        on_target_click: Callable[[], None],
    ):
        self._on_source_click = on_source_click
        self._on_target_click = on_target_click
        self._source = "Korean"
        self._target = "English"

        # Source language text
        self._source_text = ft.Text(
            self._source,
            size=44,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
        )

        # Target language text
        self._target_text = ft.Text(
            self._target,
            size=44,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
        )

        # Arrow icon
        self._arrow = ft.Icon(
            name=ft.Icons.ARROW_RIGHT_ALT,
            size=44,
            color=COLOR_TERTIARY,
        )

        # Source button area (left side)
        self._source_btn = ft.Container(
            content=self._source_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=12),
            border_radius=12,
            bgcolor=ft.Colors.TRANSPARENT,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_source_hover,
            on_click=lambda _: self._on_source_click(),
        )

        # Target button area (right side)
        self._target_btn = ft.Container(
            content=self._target_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=12),
            border_radius=12,
            bgcolor=ft.Colors.TRANSPARENT,
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            on_hover=self._on_target_hover,
            on_click=lambda _: self._on_target_click(),
        )

        # Main content layout
        main_content = ft.Row(
            [self._source_btn, self._arrow, self._target_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        )

        super().__init__(
            content=ft.Container(
                content=main_content,
                expand=True,
                alignment=ft.alignment.center,
            ),
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=ft.BoxShadow(
                blur_radius=10,
                color=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
                spread_radius=0,
            ),
        )

    def _on_source_hover(self, e):
        """Handle hover state for source language."""
        self._source_btn.bgcolor = (
            COLOR_SURFACE_TONAL if e.data == "true" else ft.Colors.TRANSPARENT
        )
        self._source_btn.update()

    def _on_target_hover(self, e):
        """Handle hover state for target language."""
        self._target_btn.bgcolor = (
            COLOR_SURFACE_TONAL if e.data == "true" else ft.Colors.TRANSPARENT
        )
        self._target_btn.update()

    def set_languages(self, source: str, target: str):
        """Update displayed languages with dynamic sizing."""
        self._source = source
        self._target = target

        # Calculate total length of both strings combined
        # "Korean" (6) + "Chinese (Simplified)" (20) = 26 chars -> Clipped at size 44 previously.
        # Target: Size 28 or lower for total length ~26.
        total_len = len(source) + len(target)

        # Fine-grained Sum-based Scaling
        if total_len < 20:
            new_size = 44  # Safe zone (e.g., English <-> Korean)
        elif total_len < 25:
            new_size = 40  # Caution zone
        elif total_len < 30:
            new_size = 32  # Danger zone (Korean + Simplified falls here)
        elif total_len < 40:
            new_size = 26  # Extreme zone (Traditional + Simplified)
        else:
            new_size = 22  # Fallback

        # Apply size to text and arrow for synchronization
        self._source_text.size = new_size
        self._target_text.size = new_size
        self._arrow.size = new_size

        self._source_text.value = source
        self._target_text.value = target

        self._source_text.update()
        self._target_text.update()
        self._arrow.update()
