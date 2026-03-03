from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components.power_button import PowerButton
from puripuly_heart.ui.theme import COLOR_SECONDARY, COLOR_TRANS_TONAL, COLOR_WARNING


def test_power_button_set_state_transitions_and_label(monkeypatch: pytest.MonkeyPatch) -> None:
    clicked = {"count": 0}
    btn = PowerButton(
        label="STT", icon="MIC", on_click=lambda: clicked.__setitem__("count", clicked["count"] + 1)
    )
    monkeypatch.setattr(type(btn), "update", lambda self: None)
    monkeypatch.setattr(type(btn._label_control), "update", lambda self: None)

    btn.set_state(False, needs_key=False)
    assert btn.bgcolor == COLOR_TRANS_TONAL
    assert btn._icon_control.color == COLOR_SECONDARY

    btn.set_state(True, needs_key=False)
    assert btn._icon_control.color == btn._label_control.color

    btn.set_state(False, needs_key=True)
    assert btn.bgcolor == COLOR_WARNING

    btn.set_label("NEW")
    assert btn._label_control.value == "NEW"
