from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.components import language_card as language_card_module
from puripuly_heart.ui.components.language_card import LanguageCard
from puripuly_heart.ui.theme import COLOR_NEUTRAL_DARK, COLOR_PRIMARY, COLOR_SECONDARY


def test_language_card_weighted_len_counts_cjk_double_width() -> None:
    assert language_card_module._weighted_len("abc") == 3
    assert language_card_module._weighted_len("한a") == 3


def test_language_card_hover_and_set_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    card = LanguageCard(
        on_source_click=lambda: None,
        on_target_click=lambda: None,
        on_swap_click=lambda: None,
    )
    monkeypatch.setattr(type(card._source_text), "update", lambda self: None)
    monkeypatch.setattr(type(card._target_text), "update", lambda self: None)
    monkeypatch.setattr(type(card._arrow_icon), "update", lambda self: None)

    card._on_source_hover(SimpleNamespace(data="true"))
    assert card._source_text.color == COLOR_PRIMARY
    card._on_source_hover(SimpleNamespace(data="false"))
    assert card._source_text.color == COLOR_NEUTRAL_DARK

    card._on_target_hover(SimpleNamespace(data="true"))
    assert card._target_text.color == COLOR_PRIMARY
    card._on_target_hover(SimpleNamespace(data="false"))
    assert card._target_text.color == COLOR_NEUTRAL_DARK

    card._on_arrow_hover(SimpleNamespace(data="true"))
    assert card._arrow_icon.color == COLOR_PRIMARY
    card._on_arrow_hover(SimpleNamespace(data="false"))
    assert card._arrow_icon.color == COLOR_SECONDARY

    card.set_languages("Korean", "English")
    assert card._source_text.size == 48
    card.set_languages("A" * 24, "B" * 24)
    assert card._source_text.size == 20
    assert card._target_text.value == "B" * 24
