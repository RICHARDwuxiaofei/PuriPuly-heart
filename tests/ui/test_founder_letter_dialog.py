from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog
from puripuly_heart.ui.i18n import get_locale, set_locale


@pytest.fixture(autouse=True)
def restore_locale_after_test():
    previous_locale = get_locale()
    yield
    set_locale(previous_locale)


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
    set_locale("ko")
    page = DummyPage()

    dialog = FounderLetterDialog(page)

    dialog.open()

    assert page.dialog is dialog._dialog
    assert dialog._acknowledge_button is not None
    assert dialog._cancel_button is not None
    assert len(page.opened) == 1
    assert dialog._cancel_button.text == "취소"
    assert dialog._acknowledge_button.text == "알겠어요"


def test_founder_letter_dialog_is_modal_to_prevent_outside_dismissal() -> None:
    set_locale("ko")
    page = DummyPage()

    dialog = FounderLetterDialog(page)

    dialog.open()

    assert dialog._dialog is not None
    assert dialog._dialog.modal is True


def test_founder_letter_dialog_buttons_only_close_modal() -> None:
    set_locale("ko")
    page = DummyPage()
    clicked: list[str] = []

    connect_dialog = FounderLetterDialog(
        page,
        on_connect=lambda: clicked.append("connect"),
        on_contact=lambda: clicked.append("contact"),
    )
    connect_dialog.open()

    assert connect_dialog._acknowledge_button is not None
    connect_dialog._acknowledge_button.on_click(None)

    assert clicked == []
    assert page.closed == [connect_dialog._dialog]
    assert page.dialog is None

    contact_dialog = FounderLetterDialog(
        page,
        on_connect=lambda: clicked.append("connect"),
        on_contact=lambda: clicked.append("contact"),
    )
    contact_dialog.open()

    assert contact_dialog._cancel_button is not None
    contact_dialog._cancel_button.on_click(None)

    assert clicked == []
    assert page.closed[-1] == contact_dialog._dialog
    assert page.dialog is None
