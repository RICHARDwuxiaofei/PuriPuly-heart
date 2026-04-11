from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.ui.views import dashboard as dashboard_module


class FakePowerButton:
    def __init__(self, label, icon, on_click, **kwargs):
        self.icon = icon
        self.kwargs = dict(kwargs)
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
        self.notice_calls: list[tuple[str | None, str | None]] = []
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

    def set_notice(self, text: str | None, tone: str | None = None) -> None:
        self.notice_calls.append((text, tone))

    def set_input_font(self, font_family: str | None) -> None:
        self.input_fonts.append(font_family)

    def apply_locale(self, display_font_family: str | None, input_font_family: str | None) -> None:
        self.locale_calls.append((display_font_family, input_font_family))


class FakeLanguageCard:
    def __init__(
        self,
        on_self_source_click,
        on_self_target_click,
        on_self_swap_click,
        on_peer_source_click,
        on_peer_target_click,
        on_peer_swap_click,
    ):
        self.on_self_source_click = on_self_source_click
        self.on_self_target_click = on_self_target_click
        self.on_self_swap_click = on_self_swap_click
        self.on_peer_source_click = on_peer_source_click
        self.on_peer_target_click = on_peer_target_click
        self.on_peer_swap_click = on_peer_swap_click
        self.languages: list[tuple[str, str, str, str]] = []
        self.row_labels: list[tuple[str, str]] = []

    def set_languages(
        self,
        self_source: str,
        self_target: str,
        peer_source: str,
        peer_target: str,
    ) -> None:
        self.languages.append((self_source, self_target, peer_source, peer_target))

    def set_row_labels(self, self_label: str, peer_label: str) -> None:
        self.row_labels.append((self_label, peer_label))


class FakeLanguageModal:
    opened: list[tuple[str, list[str]]] = []

    def __init__(self, page, languages, on_select):
        _ = (page, languages)
        self.on_select = on_select

    def open(self, *, current: str, recent: list[str]) -> None:
        self.__class__.opened.append((current, list(recent)))


class FakeManagedTrialUsageBar:
    def __init__(self, percent: int | None = None) -> None:
        self.percent = percent
        self.locale_calls = 0

    def set_percent(self, percent: int | None) -> None:
        self.percent = percent

    def apply_locale(self) -> None:
        self.locale_calls += 1


def _make_dashboard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dashboard_module, "PowerButton", FakePowerButton)
    monkeypatch.setattr(dashboard_module, "DisplayCard", FakeDisplayCard)
    monkeypatch.setattr(dashboard_module, "LanguageCard", FakeLanguageCard)
    monkeypatch.setattr(dashboard_module, "LanguageModal", FakeLanguageModal)
    monkeypatch.setattr(dashboard_module, "ManagedTrialUsageBar", FakeManagedTrialUsageBar)
    monkeypatch.setattr(dashboard_module, "create_background_glow_stack", lambda content: content)
    monkeypatch.setattr(dashboard_module, "create_glow_stack", lambda content, **_kwargs: content)
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
    lang_changes: list[tuple[str, str, str, str]] = []
    view.on_send_message = lambda source, text: sends.append((source, text))
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: lang_changes.append(
        (src, tgt, peer_src, peer_tgt)
    )

    view._on_submit("hello")
    view._on_source_select("ja")
    view._on_target_select("fr")
    view._swap_languages()

    assert sends == [("You", "hello")]
    assert view._recent_source_langs == ["ja"]
    assert view._recent_target_langs == ["fr"]
    assert lang_changes[-1] == ("fr", "ja", "", "")
    assert view.language_card.languages[-1] == ("name-fr", "name-ja", "name-fr", "name-ja")


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
    view.set_local_stt_notice("missing")
    view.set_display_text("src", language_code="ko")
    view.set_display_translation_text("dst", language_code="en")
    view.set_recent_languages(["a", "b", "c", "d", "e", "f", "g"], ["x", "y", "z"])

    assert view.is_connected is True
    assert view.display_card.statuses[-1] == ("connected", "font-en")
    assert view.display_card.display_calls[-1] == ("src", False, "font-ko")
    assert view.display_card.translation_calls[-1] == ("dst", "font-en")
    assert view.display_card.notice_calls[-1] == (
        dashboard_module.t("dashboard.local_stt_notice_missing"),
        "warning",
    )
    assert view.language_card.languages[-1] == ("name-ko", "name-en", "name-ko", "name-en")
    assert view.trans_button.states[-1] == (False, True)
    assert view.stt_button.states[-1] == (False, True)
    assert view._recent_source_langs == ["a", "b", "c", "d", "e", "f"]


def test_dashboard_builds_k2_shell_and_managed_trial_row(monkeypatch: pytest.MonkeyPatch) -> None:
    view = _make_dashboard(monkeypatch)

    shell = view.controls[0]
    assert len(shell.controls) == 2

    main_surface, managed_trial_card = shell.controls
    assert len(main_surface.controls) == 2

    left_region, right_region = main_surface.controls
    assert left_region.expand == 40
    assert right_region.expand == 60

    left_grid = left_region.content
    top_controls = [slot.content.label for slot in left_grid.controls[0].controls]
    bottom_controls = [slot.content.label for slot in left_grid.controls[1].controls]

    assert top_controls == ["STT", "PEER"]
    assert bottom_controls == ["TRANS", "OVERLAY"]
    assert view.stt_button.kwargs["icon_size"] == 80
    assert view.peer_button.kwargs["icon_size"] == 80
    assert view.trans_button.kwargs["icon_size"] == 80
    assert view.overlay_button.kwargs["icon_size"] == 80
    assert view.stt_button.kwargs["label_size"] == 32
    assert view.peer_button.kwargs["label_size"] == 32
    assert view.trans_button.kwargs["label_size"] == 32
    assert view.overlay_button.kwargs["label_size"] == 32
    assert right_region.content.controls == [view.display_card, view.language_card]
    assert managed_trial_card.visible is False


def test_dashboard_managed_trial_row_can_be_shown_without_runtime_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    shell = view.controls[0]
    managed_trial_card = shell.controls[1]

    view.set_managed_trial_state(visible=True, remaining_percent=71)

    assert view.managed_trial_state == {"visible": True, "remaining_percent": 71}
    assert managed_trial_card.visible is True

    view.set_managed_trial_state(visible=False, remaining_percent=12)

    assert view.managed_trial_state == {"visible": False, "remaining_percent": None}
    assert managed_trial_card.visible is False


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
    assert view.stt_button.label == "STT"
    assert view.peer_button.label == "PEER"
    assert view.trans_button.label == "TRANS"
    assert view.overlay_button.label == "OVERLAY"
    warning_texts = [text for text, _is_error, _font in view.display_card.display_calls]
    assert dashboard_module.t("dashboard.warn_stt_key") in warning_texts
    assert dashboard_module.t("dashboard.warn_llm_key") in warning_texts


def test_dashboard_peer_source_selection_restores_follow_self_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple[str, str, str, str]] = []
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: changes.append(
        (src, tgt, peer_src, peer_tgt)
    )
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._on_peer_source_select("ko")

    assert view._peer_source_lang_code == ""
    assert view._peer_target_lang_code == "fr"
    assert view._recent_source_langs == ["ko"]
    assert changes[-1] == ("ko", "en", "", "fr")
    assert view.language_card.languages[-1] == ("name-ko", "name-en", "name-ko", "name-fr")


def test_dashboard_peer_target_selection_restores_follow_self_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple[str, str, str, str]] = []
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: changes.append(
        (src, tgt, peer_src, peer_tgt)
    )
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._on_peer_target_select("en")

    assert view._peer_source_lang_code == "ja"
    assert view._peer_target_lang_code == ""
    assert view._recent_target_langs == ["en"]
    assert changes[-1] == ("ko", "en", "ja", "")
    assert view.language_card.languages[-1] == ("name-ko", "name-en", "name-ja", "name-en")


def test_dashboard_self_source_change_preserves_explicit_peer_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple[str, str, str, str]] = []
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: changes.append(
        (src, tgt, peer_src, peer_tgt)
    )
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._on_source_select("ja")
    view._on_source_select("de")

    assert view._peer_source_lang_code == "ja"
    assert view._peer_target_lang_code == "fr"
    assert changes[-2] == ("ja", "en", "ja", "fr")
    assert changes[-1] == ("de", "en", "ja", "fr")
    assert view.language_card.languages[-1] == ("name-de", "name-en", "name-ja", "name-fr")


def test_dashboard_peer_language_edits_share_controller_update_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple[str, str, str, str]] = []
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: changes.append(
        (src, tgt, peer_src, peer_tgt)
    )

    view._on_peer_source_select("ja")
    view._on_peer_target_select("fr")

    assert changes == [("ko", "en", "ja", ""), ("ko", "en", "ja", "fr")]


def test_dashboard_peer_swap_exchanges_source_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)
    changes: list[tuple[str, str, str, str]] = []
    view.on_language_change = lambda src, tgt, peer_src, peer_tgt: changes.append(
        (src, tgt, peer_src, peer_tgt)
    )
    view.set_languages_from_codes("ko", "en", "ja", "fr")

    view._swap_peer_languages()

    assert view._peer_source_lang_code == "fr"
    assert view._peer_target_lang_code == "ja"
    assert changes[-1] == ("ko", "en", "fr", "ja")
    assert view.language_card.languages[-1] == ("name-ko", "name-en", "name-fr", "name-ja")


def test_dashboard_self_and_peer_language_row_labels_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_module, "t", lambda key, **_kwargs: f"i18n:{key}")
    view = _make_dashboard(monkeypatch)

    assert view.language_card.row_labels[0] == (
        "i18n:dashboard.language.self",
        "i18n:dashboard.language.peer",
    )

    view.apply_locale()

    assert view.language_card.row_labels[-1] == (
        "i18n:dashboard.language.self",
        "i18n:dashboard.language.peer",
    )


def test_dashboard_local_stt_notice_can_change_and_clear_without_touching_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _make_dashboard(monkeypatch)

    view.set_local_stt_notice("missing")
    view.set_display_text("hello", language_code="ko")
    view.set_local_stt_notice("downloading", percent=63)
    view.set_local_stt_notice(None)

    assert view.display_card.display_calls == [("hello", False, "font-ko")]
    assert view.display_card.notice_calls == [
        (dashboard_module.t("dashboard.local_stt_notice_missing"), "warning"),
        (dashboard_module.t("dashboard.local_stt_notice_downloading_progress", percent=63), "info"),
        (None, None),
    ]
