from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog


class DummyPage:
    def __init__(self) -> None:
        self.dialog = None
        self.opened: list[object] = []
        self.closed: list[object] = []

    def open(self, dialog) -> None:
        self.dialog = dialog
        self.opened.append(dialog)

    def close(self, dialog) -> None:
        self.closed.append(dialog)
        if self.dialog is dialog:
            self.dialog = None


def test_founder_letter_dialog_opens_with_two_actions() -> None:
    page = DummyPage()
    clicked: list[str] = []

    dialog = FounderLetterDialog(
        page,
        on_connect=lambda: clicked.append("connect"),
        on_contact=lambda: clicked.append("contact"),
    )

    dialog.open()

    assert page.dialog is dialog._dialog
    assert dialog._connect_button is not None
    assert dialog._contact_button is not None
    assert len(page.opened) == 1
    assert dialog._connect_button.text == "OpenRouter 연결하기"
    assert dialog._contact_button.text == "저한테 연락하기"


def test_founder_letter_dialog_is_modal_to_prevent_outside_dismissal() -> None:
    page = DummyPage()

    dialog = FounderLetterDialog(
        page,
        on_connect=lambda: None,
        on_contact=lambda: None,
    )

    dialog.open()

    assert dialog._dialog is not None
    assert dialog._dialog.modal is True


def test_founder_letter_dialog_clicks_close_before_running_callbacks() -> None:
    page = DummyPage()
    clicked: list[str] = []

    connect_dialog = FounderLetterDialog(
        page,
        on_connect=lambda: clicked.append("connect"),
        on_contact=lambda: clicked.append("contact"),
    )
    connect_dialog.open()

    assert connect_dialog._connect_button is not None
    connect_dialog._connect_button.on_click(None)

    assert clicked == ["connect"]
    assert page.closed == [connect_dialog._dialog]
    assert page.dialog is None

    contact_dialog = FounderLetterDialog(
        page,
        on_connect=lambda: clicked.append("connect"),
        on_contact=lambda: clicked.append("contact"),
    )
    contact_dialog.open()

    assert contact_dialog._contact_button is not None
    contact_dialog._contact_button.on_click(None)

    assert clicked == ["connect", "contact"]
    assert page.closed[-1] == contact_dialog._dialog
    assert page.dialog is None
