from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

DIALOG_WIDTH = 720
DIALOG_HORIZONTAL_PADDING = 36
DIALOG_VERTICAL_PADDING = 34
BODY_TEXT_SIZE = 24
PARAGRAPH_SPACING = 22
ACTION_TOP_MARGIN = 32
ACTION_SPACING = 14
BUTTON_HORIZONTAL_PADDING = 30
BUTTON_VERTICAL_PADDING = 20
BUTTON_RADIUS = 20
BUTTON_TEXT_SIZE = 26


def _action_button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color={
            ft.ControlState.DEFAULT: COLOR_NEUTRAL_DARK,
            ft.ControlState.HOVERED: COLOR_PRIMARY,
        },
        bgcolor=ft.Colors.TRANSPARENT,
        padding=ft.padding.symmetric(
            horizontal=BUTTON_HORIZONTAL_PADDING,
            vertical=BUTTON_VERTICAL_PADDING,
        ),
        shape=ft.RoundedRectangleBorder(radius=BUTTON_RADIUS),
        overlay_color=ft.Colors.TRANSPARENT,
        text_style=ft.TextStyle(size=BUTTON_TEXT_SIZE, weight=ft.FontWeight.BOLD),
        animation_duration=0,
    )


@dataclass(frozen=True)
class WarmDocumentDialogResult:
    dialog: ft.AlertDialog
    primary_button: ft.TextButton
    secondary_button: ft.TextButton


def split_body_paragraphs(body: str) -> list[str]:
    return [paragraph.strip() for paragraph in body.split("\n\n") if paragraph.strip()]


def join_body_paragraphs(body_paragraphs: Sequence[str]) -> str:
    return "\n\n".join(paragraph.strip() for paragraph in body_paragraphs if paragraph.strip())


def open_warm_document_dialog(
    page: ft.Page,
    *,
    body_paragraphs: Sequence[str],
    primary_label: str,
    primary_action: Callable[[], None] | None,
    secondary_label: str,
    secondary_action: Callable[[], None] | None,
    glow_factory: Callable[[ft.Control], ft.Control] = create_glow_stack,
) -> WarmDocumentDialogResult:
    dialog: ft.AlertDialog | None = None

    def select(action: Callable[[], None] | None) -> None:
        if dialog is not None:
            page.close(dialog)
        if action is not None:
            action()

    body = ft.Column(
        controls=[
            ft.Text(
                join_body_paragraphs(body_paragraphs),
                size=BODY_TEXT_SIZE,
                color=COLOR_ON_BACKGROUND,
                selectable=True,
            )
        ],
        spacing=PARAGRAPH_SPACING,
        tight=True,
    )

    secondary_button = ft.TextButton(
        text=secondary_label,
        on_click=lambda _: select(secondary_action),
        style=_action_button_style(),
    )
    primary_button = ft.TextButton(
        text=primary_label,
        on_click=lambda _: select(primary_action),
        style=_action_button_style(),
    )
    actions = ft.Row(
        controls=[secondary_button, primary_button],
        spacing=ACTION_SPACING,
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        wrap=True,
    )

    modal_content = ft.Container(
        width=DIALOG_WIDTH,
        padding=ft.padding.symmetric(
            horizontal=DIALOG_HORIZONTAL_PADDING,
            vertical=DIALOG_VERTICAL_PADDING,
        ),
        bgcolor=COLOR_SURFACE,
        border_radius=30,
        border=ft.border.all(1, ft.Colors.with_opacity(0.35, COLOR_DIVIDER)),
        shadow=get_card_shadow(),
        content=ft.Column(
            controls=[
                body,
                ft.Container(height=ACTION_TOP_MARGIN),
                actions,
            ],
            spacing=0,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    dialog = ft.AlertDialog(
        modal=True,
        content=glow_factory(modal_content),
        content_padding=0,
        bgcolor=ft.Colors.TRANSPARENT,
        surface_tint_color=ft.Colors.TRANSPARENT,
    )
    page.open(dialog)
    return WarmDocumentDialogResult(
        dialog=dialog,
        primary_button=primary_button,
        secondary_button=secondary_button,
    )
