from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components import display_card as display_card_module
from puripuly_heart.ui.components.display_card import DisplayCard


def test_display_card_helpers_cover_length_and_status_labels() -> None:
    assert display_card_module._weighted_len("abc") == 3
    assert display_card_module._weighted_len("한a") == 3
    assert display_card_module._display_size_for_length(8) == 48
    assert display_card_module._display_size_for_length(16) == 40
    assert display_card_module._display_size_for_length(28) == 34
    assert display_card_module._display_size_for_length(40) == 28
    assert display_card_module._display_size_for_length(80) == 24
    assert display_card_module._status_label("connecting") == display_card_module.t(
        "display.connecting"
    )
    assert display_card_module._status_label("connected") == display_card_module.t(
        "display.connected"
    )
    assert display_card_module._status_label("stopping") == display_card_module.t(
        "display.stopping"
    )
    assert display_card_module._status_label("other") == display_card_module.t(
        "display.disconnected"
    )


def test_display_card_submit_and_state_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    card = DisplayCard(on_submit=lambda text: submitted.append(text))
    monkeypatch.setattr(type(card._input_field), "update", lambda self: None)

    event = SimpleNamespace(
        control=SimpleNamespace(
            value="  hello  ",
            update=lambda: None,
            focus=lambda: submitted.append("focused"),
        )
    )
    card._handle_submit(event)
    assert submitted == ["hello", "focused"]
    assert event.control.value == ""

    card._handle_submit(
        SimpleNamespace(
            control=SimpleNamespace(value="   ", update=lambda: None, focus=lambda: None)
        )
    )
    assert submitted == ["hello", "focused"]

    card.set_display("primary", is_error=False, font_family="font-a")
    assert card._display_primary.value == "primary"
    assert card._display_secondary.visible is False
    assert card._display_primary.font_family == "font-a"

    card.set_display_translation("secondary", font_family="font-b")
    assert card._display_secondary.value == "secondary"
    assert card._display_secondary.visible is True
    assert card._display_secondary.font_family == "font-b"

    card.set_status("connected", font_family="font-c")
    assert card._showing_status is True
    assert card._display_primary.value == display_card_module.t("display.connected")
    assert card._display_primary.font_family == "font-c"


def test_display_card_input_font_locale_and_sync_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    card = DisplayCard(on_submit=lambda _text: None)
    monkeypatch.setattr(type(card._input_field), "update", lambda self: None)
    monkeypatch.setattr(type(card._display_primary), "update", lambda self: None)
    monkeypatch.setattr(type(card._display_secondary), "update", lambda self: None)

    card.set_input_font(None)
    assert card._input_field.text_style.font_family == ""

    card.set_display("x" * 50, font_family="font-long")
    assert card._display_primary.size == 24

    card.set_display_translation(None)
    assert card._display_secondary.visible is False

    card._showing_status = True
    card._status = "stopping"
    card.apply_locale(display_font_family="display-font", input_font_family="input-font")

    assert card._input_field.hint_text == display_card_module.t("display.input_hint")
    assert card._input_field.hint_style.font_family == "display-font"
    assert card._input_field.text_style.font_family == "input-font"
    assert card._display_primary.value == display_card_module.t("display.stopping")

    card.clear_input()
    assert card._input_field.value == ""
