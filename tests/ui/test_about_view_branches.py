from __future__ import annotations

import types

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.views import about as about_module
from puripuly_heart.ui.views.about import AboutView


def _collect_click_handlers(control) -> list:
    handlers = []
    on_click = getattr(control, "on_click", None)
    if callable(on_click):
        handlers.append(on_click)
    content = getattr(control, "content", None)
    if content is not None:
        handlers.extend(_collect_click_handlers(content))
    controls = getattr(control, "controls", None)
    if controls:
        for child in controls:
            handlers.extend(_collect_click_handlers(child))
    return handlers


def _row_cards(container) -> list:
    return [child.content for child in container.content.controls]


def test_about_view_uses_shared_card_wrapper_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper

    monkeypatch.setattr(about_module, "_get_profile_image_path", lambda: "")
    monkeypatch.setattr(about_module, "_load_third_party_notices", lambda: "licenses")

    view = AboutView()

    default_cards = [*_row_cards(view.controls[0]), *_row_cards(view.controls[1])]
    full_width_cards = [view.controls[2], view.controls[3]]

    assert len(default_cards) == 4
    assert all(isinstance(card, SharedCardWrapper) for card in default_cards)
    assert {card.height for card in default_cards} == {300}
    assert all(card.expand is True for card in default_cards)
    assert all(isinstance(card, SharedCardWrapper) for card in full_width_cards)
    assert all(card.height is None for card in full_width_cards)
    assert all(card.expand is False for card in full_width_cards)
    assert all(card.content.expand is False for card in full_width_cards)
    assert all(card.content.controls[1].expand is False for card in full_width_cards)


def test_about_view_link_actions_handle_missing_page_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(about_module, "_get_profile_image_path", lambda: "")
    monkeypatch.setattr(about_module, "_load_third_party_notices", lambda: "licenses")
    monkeypatch.setattr(about_module.webbrowser, "open", lambda url: opened.append(url))

    view = AboutView()
    click_handlers = []
    for control in view.controls:
        click_handlers.extend(_collect_click_handlers(control))

    for handler in click_handlers:
        handler(None)

    assert "https://github.com/kapitalismho/PuriPuly-heart" in opened
    assert "https://discord.com/users/377814093182140416" in opened
    assert "https://github.com/misyaguziya/VRCT" in opened
    assert "https://github.com/naeruru/mimiuchi" in opened
    assert "https://github.com/febilly/Yakutan" in opened


def test_about_view_hover_handlers_and_locale_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(about_module, "_get_profile_image_path", lambda: "")
    monkeypatch.setattr(about_module, "_load_third_party_notices", lambda: "licenses")
    monkeypatch.setattr(AboutView, "update", lambda self: setattr(self, "_updated", True))
    view = AboutView()

    text = types.SimpleNamespace(color=about_module.COLOR_ON_BACKGROUND, update=lambda: None)
    evt = types.SimpleNamespace(control=types.SimpleNamespace(content=text), data="true")
    view._on_name_hover(evt)
    assert text.color == about_module.COLOR_PRIMARY
    evt.data = "false"
    view._on_link_hover(evt)
    assert text.color == about_module.COLOR_ON_BACKGROUND
    evt.data = "true"
    view._on_version_hover(evt)
    assert text.color == about_module.COLOR_PRIMARY
    evt.data = "false"
    view._on_thanks_hover(evt)
    assert text.color == about_module.COLOR_ON_BACKGROUND

    view.apply_locale()
    assert getattr(view, "_updated", False) is True


def test_about_helper_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenFiles:
        def joinpath(self, _name):
            raise RuntimeError("missing")

    monkeypatch.setattr(about_module.resources, "files", lambda _name: BrokenFiles())

    assert about_module._load_third_party_notices() == "Could not load license information."
    assert about_module._get_profile_image_path() == ""
