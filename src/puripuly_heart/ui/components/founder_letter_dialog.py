from __future__ import annotations

from typing import Callable

import flet as ft

from puripuly_heart.ui.components.glow import create_glow_stack
from puripuly_heart.ui.components.warm_document_dialog import open_warm_document_dialog
from puripuly_heart.ui.i18n import t


class FounderLetterDialog:
    def __init__(
        self,
        page: ft.Page,
        *,
        on_connect: Callable[[], None],
        on_contact: Callable[[], None],
    ) -> None:
        self._page = page
        self._on_connect = on_connect
        self._on_contact = on_contact
        self._dialog: ft.AlertDialog | None = None
        self._connect_button: ft.TextButton | None = None
        self._contact_button: ft.TextButton | None = None

    def open(self) -> None:
        paragraphs = [
            t("openrouter.handoff.letter.p1"),
            t("openrouter.handoff.letter.p2"),
            t("openrouter.handoff.letter.p3"),
        ]
        result = open_warm_document_dialog(
            self._page,
            body_paragraphs=paragraphs,
            primary_label=t("openrouter.handoff.connect"),
            primary_action=self._on_connect,
            secondary_label=t("openrouter.handoff.contact"),
            secondary_action=self._on_contact,
            glow_factory=create_glow_stack,
        )
        self._dialog = result.dialog
        self._connect_button = result.primary_button
        self._contact_button = result.secondary_button
