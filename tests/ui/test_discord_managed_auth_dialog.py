from __future__ import annotations

from collections.abc import Callable

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.discord_managed_auth_dialog import DiscordManagedAuthDialog
from puripuly_heart.ui.i18n import set_locale, t


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


def _dialog(
    page: DummyPage,
    events: list[str] | None = None,
    *,
    on_cancel: Callable[[], None] | None = None,
) -> DiscordManagedAuthDialog:
    calls = events if events is not None else []
    return DiscordManagedAuthDialog(
        page,
        on_continue=lambda: calls.append("continue"),
        on_byok=lambda: calls.append("byok"),
        on_close=lambda: calls.append("close"),
        on_reopen_browser=lambda: calls.append("reopen"),
        on_cancel=on_cancel,
    )


def test_discord_managed_auth_dialog_declares_initial_action_labels() -> None:
    page = DummyPage()

    dialog = _dialog(page)

    assert dialog.action_labels == [
        "discord_auth.continue",
        "discord_auth.byok",
        "discord_auth.close",
    ]


def test_discord_managed_auth_dialog_waiting_state_uses_waiting_labels() -> None:
    set_locale("en")
    page = DummyPage()
    dialog = _dialog(page)

    dialog.open()
    dialog.set_waiting()

    assert dialog.waiting_action_labels == [
        "discord_auth.reopen_browser",
        "discord_auth.cancel",
    ]
    assert dialog._title_text is not None
    assert dialog._body_text is not None
    assert dialog._title_text.value == t("discord_auth.waiting_title")
    assert dialog._body_text.value == t("discord_auth.waiting_body")
    assert dialog._reopen_browser_button is not None
    assert dialog._cancel_button is not None
    assert [control.text for control in dialog._actions.controls] == [
        t("discord_auth.reopen_browser"),
        t("discord_auth.cancel"),
    ]


def test_discord_managed_auth_dialog_opens_one_modal_dialog_on_page() -> None:
    set_locale("en")
    page = DummyPage()
    dialog = _dialog(page)

    dialog.open()

    assert page.dialog is dialog._dialog
    assert len(page.opened) == 1
    assert dialog._dialog is not None
    assert dialog._dialog.modal is True
    assert dialog._continue_button is not None
    assert dialog._byok_button is not None
    assert dialog._close_button is not None


def test_discord_managed_auth_dialog_continue_does_not_close_before_callback() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = _dialog(page, events)
    dialog.open()

    assert dialog._continue_button is not None
    dialog._continue_button.on_click(None)

    assert events == ["continue"]
    assert page.closed == []
    assert page.dialog is dialog._dialog


def test_discord_managed_auth_dialog_byok_and_close_close_then_invoke_callbacks() -> None:
    page = DummyPage()
    events: list[str] = []
    byok_dialog = _dialog(page, events)
    byok_dialog.open()

    assert byok_dialog._byok_button is not None
    byok_dialog._byok_button.on_click(None)

    assert events == ["byok"]
    assert page.closed == [byok_dialog._dialog]
    assert page.dialog is None

    close_dialog = _dialog(page, events)
    close_dialog.open()

    assert close_dialog._close_button is not None
    close_dialog._close_button.on_click(None)

    assert events == ["byok", "close"]
    assert page.closed[-1] == close_dialog._dialog
    assert page.dialog is None


def test_discord_managed_auth_dialog_waiting_reopen_and_cancel_behavior() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = _dialog(page, events, on_cancel=lambda: events.append("cancel"))
    dialog.open()
    dialog.set_waiting()

    assert dialog._reopen_browser_button is not None
    dialog._reopen_browser_button.on_click(None)

    assert events == ["reopen"]
    assert page.closed == []
    assert page.dialog is dialog._dialog

    assert dialog._cancel_button is not None
    dialog._cancel_button.on_click(None)

    assert events == ["reopen", "cancel"]
    assert page.closed == [dialog._dialog]
    assert page.dialog is None


def test_discord_managed_auth_dialog_waiting_cancel_falls_back_to_close_callback() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = _dialog(page, events)
    dialog.open()
    dialog.set_waiting()

    assert dialog._cancel_button is not None
    dialog._cancel_button.on_click(None)

    assert events == ["close"]
    assert page.closed == [dialog._dialog]


def test_discord_managed_auth_dialog_close_is_idempotent() -> None:
    page = DummyPage()
    dialog = _dialog(page)
    dialog.open()

    dialog.close()
    dialog.close()

    assert page.closed == [dialog._dialog]
    assert page.dialog is None
