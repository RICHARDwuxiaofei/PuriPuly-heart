from __future__ import annotations

from collections.abc import Callable

import pytest

pytest.importorskip("flet")

import flet as ft  # noqa: E402

import puripuly_heart.ui.components.discord_managed_auth_dialog as discord_module  # noqa: E402
from puripuly_heart.ui.components.discord_managed_auth_dialog import DiscordManagedAuthDialog
from puripuly_heart.ui.i18n import set_locale, t
from puripuly_heart.ui.theme import (  # noqa: E402
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
)


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
    on_reopen_browser: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> DiscordManagedAuthDialog:
    calls = events if events is not None else []
    return DiscordManagedAuthDialog(
        page,
        on_continue=lambda: calls.append("continue"),
        on_byok=lambda: calls.append("byok"),
        on_close=lambda: calls.append("close"),
        on_reopen_browser=on_reopen_browser or (lambda: calls.append("reopen")),
        on_cancel=on_cancel,
    )


def _dialog_without_reopen(
    page: DummyPage,
    events: list[str] | None = None,
) -> DiscordManagedAuthDialog:
    calls = events if events is not None else []
    return DiscordManagedAuthDialog(
        page,
        on_continue=lambda: calls.append("continue"),
        on_byok=lambda: calls.append("byok"),
        on_close=lambda: calls.append("close"),
    )


def _modal_content(page: DummyPage):
    return page.dialog.content


def _content_column(page: DummyPage):
    return _modal_content(page).content


def _body_column(page: DummyPage):
    for control in _content_column(page).controls:
        if control.__class__.__name__ == "Column":
            return control
    raise AssertionError("dialog content did not include a body column")


def _action_row(page: DummyPage):
    return _content_column(page).controls[-1]


def _button_text_size(button) -> int:
    text_style = next(iter(button.style.text_style.values()))
    return text_style.size


def test_discord_managed_auth_dialog_declares_initial_action_labels() -> None:
    page = DummyPage()

    dialog = _dialog(page)

    assert dialog.action_labels == [
        "discord_auth.close",
        "discord_auth.continue",
    ]


def test_discord_managed_auth_dialog_uses_warm_document_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_keys: list[str] = []

    def fake_t(key: str) -> str:
        requested_keys.append(key)
        return f"value:{key}"

    monkeypatch.setattr(discord_module, "t", fake_t)
    monkeypatch.setattr(discord_module, "create_glow_stack", lambda content: content)

    page = DummyPage()
    dialog = DiscordManagedAuthDialog(
        page,
        on_continue=lambda: None,
        on_byok=lambda: None,
        on_close=lambda: None,
        on_reopen_browser=lambda: None,
    )

    dialog.open()

    modal_content = _modal_content(page)
    assert "discord_auth.title" not in requested_keys
    assert "discord_auth.requirements" not in requested_keys
    assert "discord_auth.byok" not in requested_keys
    assert modal_content.width == 720
    assert modal_content.height is None

    body_column = _body_column(page)
    body_text = body_column.controls[0]
    action_row = _action_row(page)

    assert len(body_column.controls) == 1
    assert body_text.value == "value:discord_auth.body"
    assert body_text.size == 24
    assert body_text.selectable is True
    assert [button.__class__.__name__ for button in action_row.controls] == [
        "TextButton",
        "TextButton",
    ]
    assert [button.text for button in action_row.controls] == [
        "value:discord_auth.close",
        "value:discord_auth.continue",
    ]
    assert [_button_text_size(button) for button in action_row.controls] == [26, 26]
    assert [button.style.color[ft.ControlState.DEFAULT] for button in action_row.controls] == [
        COLOR_NEUTRAL_DARK,
        COLOR_NEUTRAL_DARK,
    ]
    assert [button.style.color[ft.ControlState.HOVERED] for button in action_row.controls] == [
        COLOR_PRIMARY,
        COLOR_PRIMARY,
    ]
    assert [button.style.animation_duration for button in action_row.controls] == [0, 0]


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
    assert dialog._body_text is not None
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
    assert dialog._byok_button is None
    assert dialog._close_button is not None
    assert dialog._actions is not None
    assert dialog._close_button is dialog._actions.controls[0]
    assert dialog._continue_button is dialog._actions.controls[1]


def test_discord_managed_auth_dialog_open_is_idempotent() -> None:
    page = DummyPage()
    dialog = _dialog(page)

    dialog.open()
    first_dialog = dialog._dialog
    dialog.open()

    assert dialog._dialog is first_dialog
    assert page.opened == [first_dialog]
    assert page.closed == []
    assert page.dialog is first_dialog


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


def test_discord_managed_auth_dialog_close_closes_then_invokes_callback() -> None:
    page = DummyPage()
    events: list[str] = []
    close_dialog = DiscordManagedAuthDialog(
        page,
        on_continue=lambda: events.append("continue"),
        on_byok=lambda: events.append(f"byok_closed={page.dialog is None}"),
        on_close=lambda: events.append(f"close_closed={page.dialog is None}"),
        on_reopen_browser=lambda: events.append("reopen"),
    )
    close_dialog.open()

    assert close_dialog._close_button is not None
    close_dialog._close_button.on_click(None)

    assert events == ["close_closed=True"]
    assert page.closed[-1] == close_dialog._dialog
    assert page.dialog is None


def test_discord_managed_auth_dialog_waiting_reopen_and_cancel_behavior() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = DiscordManagedAuthDialog(
        page,
        on_continue=lambda: events.append("continue"),
        on_byok=lambda: events.append("byok"),
        on_close=lambda: events.append(f"close_closed={page.dialog is None}"),
        on_reopen_browser=lambda: events.append(f"reopen_closed={page.dialog is None}"),
        on_cancel=lambda: events.append(f"cancel_closed={page.dialog is None}"),
    )
    dialog.open()
    dialog.set_waiting()

    assert dialog._reopen_browser_button is not None
    dialog._reopen_browser_button.on_click(None)

    assert events == ["reopen_closed=False"]
    assert page.closed == []
    assert page.dialog is dialog._dialog

    assert dialog._cancel_button is not None
    dialog._cancel_button.on_click(None)

    assert events == ["reopen_closed=False", "cancel_closed=True"]
    assert page.closed == [dialog._dialog]
    assert page.dialog is None


def test_discord_managed_auth_dialog_hides_reopen_when_callback_is_absent() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = _dialog_without_reopen(page, events)
    dialog.open()

    dialog.set_waiting()

    assert dialog._reopen_browser_button is None
    assert dialog._cancel_button is not None
    assert [control.text for control in dialog._actions.controls] == [t("discord_auth.cancel")]


def test_discord_managed_auth_dialog_waiting_cancel_falls_back_to_close_callback() -> None:
    page = DummyPage()
    events: list[str] = []
    dialog = DiscordManagedAuthDialog(
        page,
        on_continue=lambda: events.append("continue"),
        on_byok=lambda: events.append("byok"),
        on_close=lambda: events.append(f"close_closed={page.dialog is None}"),
        on_reopen_browser=lambda: events.append("reopen"),
    )
    dialog.open()
    dialog.set_waiting()

    assert dialog._cancel_button is not None
    dialog._cancel_button.on_click(None)

    assert events == ["close_closed=True"]
    assert page.closed == [dialog._dialog]
    assert page.dialog is None


def test_discord_managed_auth_dialog_close_is_idempotent() -> None:
    page = DummyPage()
    dialog = _dialog(page)
    dialog.open()

    dialog.close()
    dialog.close()

    assert page.closed == [dialog._dialog]
    assert page.dialog is None
