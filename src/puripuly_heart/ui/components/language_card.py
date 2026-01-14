from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    COLOR_TERTIARY,
    get_card_shadow,
)

# CJK (Chinese, Japanese, Korean) characters start at this Unicode point
_CJK_START = 0x3000


def _weighted_len(text: str) -> float:
    """Calculate weighted length for CJK-aware font sizing.

    CJK characters are rendered ~2x wider than Latin characters,
    so we weight them accordingly for accurate size calculations.
    """
    return sum(2 if ord(c) >= _CJK_START else 1 for c in text)


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
            no_wrap=True,  # Prevent text from wrapping to next line
            overflow=ft.TextOverflow.ELLIPSIS,  # Fallback: truncate with ... if still too long
        )

        # Target language text
        self._target_text = ft.Text(
            self._target,
            size=44,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
            no_wrap=True,  # Prevent text from wrapping to next line
            overflow=ft.TextOverflow.ELLIPSIS,  # Fallback: truncate with ... if still too long
        )

        # Arrow icon (Wrapped in container to match text padding for alignment)
        self._arrow_icon = ft.Icon(
            name=ft.Icons.ARROW_RIGHT_ALT,
            size=44,
            color=COLOR_TERTIARY,
        )
        self._arrow = ft.Container(
            content=self._arrow_icon,
            padding=ft.padding.symmetric(vertical=12),
        )

        # Source button area (left side)
        self._source_btn = ft.Container(
            content=self._source_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=12),
            border_radius=12,
            bgcolor=ft.Colors.TRANSPARENT,
            on_hover=self._on_source_hover,
            on_click=lambda _: self._on_source_click(),
            expand_loose=True,  # Fill space but give loose constraints to child
            alignment=ft.alignment.center,
        )

        # Target button area (right side)
        self._target_btn = ft.Container(
            content=self._target_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=12),
            border_radius=12,
            bgcolor=ft.Colors.TRANSPARENT,
            on_hover=self._on_target_hover,
            on_click=lambda _: self._on_target_click(),
            expand_loose=True,  # Fill space but give loose constraints to child
            alignment=ft.alignment.center,
        )

        # Main content layout
        main_content = ft.Row(
            [self._source_btn, self._arrow, self._target_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        )

        # Use the reusable glow stack wrapper
        # The content container handles formatting
        content_with_glow = create_glow_stack(
            ft.Container(
                content=main_content,
                expand=True,
                alignment=ft.alignment.center,
            )
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

    def _on_source_hover(self, e):
        """Handle hover state for source language."""
        self._source_text.color = COLOR_PRIMARY if e.data == "true" else COLOR_NEUTRAL_DARK
        self._source_text.update()

    def _on_target_hover(self, e):
        """Handle hover state for target language."""
        self._target_text.color = COLOR_PRIMARY if e.data == "true" else COLOR_NEUTRAL_DARK
        self._target_text.update()

    def set_languages(self, source: str, target: str):
        """Update displayed languages with dynamic sizing."""
        self._source = source
        self._target = target

        # Calculate weighted total length (CJK chars count as 1.5x)
        total_len = _weighted_len(source) + _weighted_len(target)

        # Fine-grained Sum-based Scaling (adjusted for weighted length)
        if total_len < 20:
            new_size = 44  # Safe zone (e.g., English ↔ Korean)
        elif total_len < 28:
            new_size = 38  # Caution zone
        elif total_len < 36:
            new_size = 32  # Danger zone
        elif total_len < 44:
            new_size = 26  # Extreme zone
        else:
            new_size = 22  # Fallback

        # Apply size to text and arrow for synchronization
        self._source_text.size = new_size
        self._target_text.size = new_size
        self._arrow_icon.size = new_size

        self._source_text.value = source
        self._target_text.value = target

        if self._source_text.page is not None:
            self._source_text.update()
        if self._target_text.page is not None:
            self._target_text.update()
        if self._arrow_icon.page is not None:
            self._arrow_icon.update()
