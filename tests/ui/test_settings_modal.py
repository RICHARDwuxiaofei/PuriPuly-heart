from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.settings.settings_modal import (
    OptionItem,
    SettingsModal,
)


class DummyPage:
    def __init__(self) -> None:
        self.opened: list[object] = []

    def open(self, dialog) -> None:
        self.opened.append(dialog)


def test_settings_modal_open_uses_true_modal_dialog() -> None:
    page = DummyPage()
    modal = SettingsModal(
        page=page,
        title="Title",
        options=[OptionItem(value="on", label="On"), OptionItem(value="off", label="Off")],
        on_select=lambda _value: None,
    )

    modal.open(current="on")

    assert len(page.opened) == 1
    assert modal._dialog is not None
    assert modal._dialog.modal is True
