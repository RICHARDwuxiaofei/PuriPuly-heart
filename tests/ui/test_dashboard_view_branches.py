from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.views import dashboard as dashboard_module


class FakePowerButton:
    def __init__(self, label, icon, on_click, **kwargs):
        _ = (icon, kwargs)
        self.label = label
        self.on_click = on_click
        self.states: list[tuple[bool, bool]] = []

    def set_state(self, is_on: bool, needs_key: bool = False):
        self.states.append((is_on, needs_key))

    def set_label(self, label: str) -> None:
        self.label = label


class FakeDisplayCard:
    def __init__(self, on_submit):
        self._on_submit = on_submit
        self.statuses: list[tuple[str, str | None]] = []
        self.display_calls: list[tuple[str, bool, str | None]] = []
        self.translation_calls: list[tuple[str | None, str | None]] = []
        self.input_fonts: list[str | None] = []
        self.locale_calls: list[tuple[str | None, str | None]] = []

    def set_status(self, status: str, font_family: str | None = None) -> None:
        self.statuses.append((status, font_family))

    def set_display(
        self, text: str, *, is_error: bool = False, font_family: str | None = None
    ) -> None:
        self.display_calls.append((text, is_error, font_family))

    def set_display_translation(self, text: str | None, font_family: str | None = None) -> None:
        self.translation_calls.append((text, font_family))

    def set_input_font(self, font_family: str | None) -> None:
        self.input_fonts.append(font_family)

    def apply_locale(self, display_font_family: str | None, input_font_family: str | None) -> None:
        self.locale_calls.append((display_font_family, input_font_family))


class FakeLanguageCard:
    def __init__(self, on_source_click, on_target_click, on_swap_click):
        self.on_source_click = on_source_click
        self.on_target_click = on_target_click
        self.on_swap_click = on_swap_click
        self.languages: list[tuple[str, str]] = []

    def set_languages(self, source: str, target: str) -> None:
        self.languages.append((source, target))


class FakeLanguageModal:
    opened: list[tuple[str, list[str]]] = []

    def __init__(self, page, languages, on_select):
        _ = (page, languages)
        self.on_select = on_select

    def open(self, *, current: str, recent: list[str]) -> None:
        self.__class__.opened.append((current, list(recent)))


def _make_dashboard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dashboard_module, "PowerButton", FakePowerButton)
    monkeypatch.setattr(dashboard_module, "DisplayCard", FakeDisplayCard)
    monkeypatch.setattr(dashboard_module, "LanguageCard", FakeLanguageCard)
    monkeypatch.setattr(dashboard_module, "LanguageModal", FakeLanguageModal)
    monkeypatch.setattr(dashboard_module, "create_background_glow_stack", lambda content: content)
    monkeypatch.setattr(dashboard_module, "font_for_language", lambda code: f"font-{code}")
    monkeypatch.setattr(dashboard_module, "language_name", lambda code: f"name-{code}")
    monkeypatch.setattr(dashboard_module, "get_locale", lambda: "en")
    view = dashboard_module.DashboardView()
    FakeLanguageModal.opened = []
    return view


def test_dashboard_stt_toggle_warning_and_enable_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    seen: list[bool] = []
    view.on_toggle_stt = lambda enabled: seen.append(enabled)
    view.stt_needs_key = True

    view._toggle_stt()
    view._toggle_stt()
    view.stt_needs_key = False
    view._toggle_stt()
    view._toggle_stt()

    assert seen == [False, False, True, False]
    assert view.is_stt_on is False
    assert view._stt_showing_warning is False
    assert any(
        call[0] == dashboard_module.t("dashboard.warn_stt_key")
        for call in view.display_card.display_calls
    )


def test_dashboard_translation_toggle_controls_power_state(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    seen: list[bool] = []
    view.on_toggle_translation = lambda enabled: seen.append(enabled)
    view.translation_needs_key = True

    view._toggle_translation()
    view._toggle_translation()
    view.translation_needs_key = False
    view._toggle_translation()
    view._toggle_translation()

    assert seen == [False, False, True, False]
    assert view.is_power_on is False
    assert any(
        call[0] == dashboard_module.t("dashboard.warn_llm_key")
        for call in view.display_card.display_calls
    )


def test_dashboard_submit_and_language_selection_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    sends: list[tuple[str, str]] = []
    lang_changes: list[tuple[str, str]] = []
    view.on_send_message = lambda source, text: sends.append((source, text))
    view.on_language_change = lambda src, tgt: lang_changes.append((src, tgt))

    view._on_submit("hello")
    view._on_source_select("ja")
    view._on_target_select("fr")
    view._swap_languages()

    assert sends == [("You", "hello")]
    assert view._recent_source_langs == ["ja"]
    assert view._recent_target_langs == ["fr"]
    assert lang_changes[-1] == ("fr", "ja")
    assert view.language_card.languages[-1] == ("name-fr", "name-ja")


def test_dashboard_recent_languages_caps_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    persisted: list[tuple[list[str], list[str]]] = []
    view.on_recent_languages_change = lambda src, tgt: persisted.append((list(src), list(tgt)))

    for idx in range(8):
        view._add_to_recent(f"s{idx}", is_source=True)
        view._add_to_recent(f"t{idx}", is_source=False)

    assert len(view._recent_source_langs) == 6
    assert len(view._recent_target_langs) == 6
    assert view._recent_source_langs[0] == "s7"
    assert view._recent_source_langs[-1] == "s2"
    assert persisted


def test_dashboard_public_setters_update_components(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    view.set_status("connected")
    view.set_languages_from_codes("ko", "en")
    view.set_translation_enabled(False)
    view.set_stt_enabled(False)
    view.set_translation_needs_key(True, update_ui=True)
    view.set_stt_needs_key(True, update_ui=True)
    view.set_display_text("src", language_code="ko")
    view.set_display_translation_text("dst", language_code="en")
    view.set_recent_languages(["a", "b", "c", "d", "e", "f", "g"], ["x", "y", "z"])

    assert view.is_connected is True
    assert view.display_card.statuses[-1] == ("connected", "font-en")
    assert view.display_card.display_calls[-1] == ("src", False, "font-ko")
    assert view.display_card.translation_calls[-1] == ("dst", "font-en")
    assert view.trans_button.states[-1] == (False, True)
    assert view.stt_button.states[-1] == (False, True)
    assert view._recent_source_langs == ["a", "b", "c", "d", "e", "f"]


def test_dashboard_apply_locale_and_dialog_open_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)
    view.page = object()
    view._stt_showing_warning = True
    view._open_source_dialog()
    view._open_target_dialog()
    view.apply_locale()
    view._translation_showing_warning = True
    view._stt_showing_warning = False
    view.apply_locale()

    assert FakeLanguageModal.opened[0][0] == "ko"
    assert FakeLanguageModal.opened[1][0] == "en"
    assert view.stt_button.label == dashboard_module.t("dashboard.stt_label")
    assert view.trans_button.label == dashboard_module.t("dashboard.trans_label")
    warning_texts = [text for text, _is_error, _font in view.display_card.display_calls]
    assert dashboard_module.t("dashboard.warn_stt_key") in warning_texts
    assert dashboard_module.t("dashboard.warn_llm_key") in warning_texts
