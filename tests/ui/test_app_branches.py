from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

import puripuly_heart.ui.app as app_module
from puripuly_heart.ui.app import TranslatorApp, _check_and_notify_update


class DummyPage:
    def __init__(self) -> None:
        self.opened: list[object] = []
        self.tasks: list[object] = []
        self.title: str = ""
        self.theme = None
        self.updated = 0
        self.theme_mode = None
        self.bgcolor = None
        self.padding = None
        self.added: list[object] = []
        self.window = SimpleNamespace(
            frameless=False,
            resizable=False,
            width=0,
            height=0,
            min_width=0,
            min_height=0,
            icon="",
        )

    def open(self, control) -> None:
        self.opened.append(control)

    def run_task(self, coro_fn) -> None:
        self.tasks.append(coro_fn)

    def update(self) -> None:
        self.updated += 1

    def add(self, control) -> None:
        self.added.append(control)


class DummyContent:
    def __init__(self, content=None) -> None:
        self.content = content
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


def test_translator_app_init_builds_layout_and_wires_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyController:
        def __init__(self, page, app, config_path):
            self.page = page
            self.app = app
            self.config_path = config_path
            self.settings = None

    monkeypatch.setattr(app_module, "GuiController", DummyController)
    monkeypatch.setattr(app_module, "register_fonts", lambda _page: None)
    monkeypatch.setattr(app_module, "get_app_theme", lambda **_kwargs: "theme")
    monkeypatch.setattr(app_module, "font_for_language", lambda _code: "font")
    monkeypatch.setattr(app_module, "get_locale", lambda: "en")

    page = DummyPage()
    app = TranslatorApp(page, config_path=Path("settings.json"))

    assert app.controller.config_path == Path("settings.json")
    assert page.title == app_module.t("app.title")
    assert page.window.frameless is True
    assert page.window.resizable is True
    assert page.window.width == 960
    assert page.window.height == 780
    assert page.added
    assert app.view_dashboard.on_send_message == app._on_manual_submit
    assert app.view_settings.on_verify_api_key == app._on_verify_api_key


@pytest.mark.asyncio
async def test_on_nav_change_applies_provider_changes_when_leaving_settings() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app._current_tab = 1
    app.view_dashboard = object()
    app.view_logs = SimpleNamespace(scroll_to_bottom=lambda: asyncio.sleep(0))
    app.view_about = object()
    app.view_settings = SimpleNamespace(
        has_provider_changes=True,
        provider_change_requires_pipeline=True,
        refresh_prompt_if_empty=lambda: None,
    )
    app.content_area = DummyContent()
    seen: list[bool] = []

    async def fake_apply_providers(*, rebuild_stt: bool = True) -> None:
        seen.append(rebuild_stt)

    app.controller = SimpleNamespace(apply_providers=fake_apply_providers)

    app._on_nav_change(0)
    assert app.content_area.content is app.view_dashboard
    assert app.view_settings.has_provider_changes is False
    assert app.view_settings.provider_change_requires_pipeline is False
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert seen == [True]


@pytest.mark.asyncio
async def test_on_nav_change_refreshes_prompt_and_schedules_log_scroll() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app._current_tab = 0
    refreshed = {"count": 0}
    scrolled = {"count": 0}

    async def fake_scroll_to_bottom():
        scrolled["count"] += 1

    app.view_dashboard = object()
    app.view_settings = SimpleNamespace(
        has_provider_changes=False,
        provider_change_requires_pipeline=False,
        refresh_prompt_if_empty=lambda: refreshed.__setitem__("count", refreshed["count"] + 1),
    )
    app.view_logs = SimpleNamespace(scroll_to_bottom=fake_scroll_to_bottom)
    app.view_about = object()
    app.content_area = DummyContent()
    app.controller = SimpleNamespace(apply_providers=lambda **kwargs: asyncio.sleep(0))

    app._on_nav_change(1)
    assert app.content_area.content is app.view_settings
    assert refreshed["count"] == 1

    app._on_nav_change(2)
    assert app.content_area.content is app.view_logs
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert scrolled["count"] == 1


def test_apply_locale_updates_views_and_page() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app.title_bar = SimpleNamespace(set_title=lambda value: setattr(app, "_title", value))
    view_calls: list[str] = []
    app.view_dashboard = SimpleNamespace(apply_locale=lambda: view_calls.append("dash"))
    app.view_settings = SimpleNamespace(apply_locale=lambda: view_calls.append("settings"))
    app.view_logs = SimpleNamespace(apply_locale=lambda: view_calls.append("logs"))

    app.apply_locale()

    assert app.page.title == app_module.t("app.title")
    assert view_calls == ["dash", "settings", "logs"]
    assert app.page.updated == 1


@pytest.mark.asyncio
async def test_submit_toggle_and_settings_wrappers_schedule_controller_tasks() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    seen: list[tuple[str, object]] = []

    async def fake_submit(text: str) -> None:
        seen.append(("submit", text))

    async def fake_translation(enabled: bool) -> None:
        seen.append(("translation", enabled))

    async def fake_stt(enabled: bool) -> None:
        seen.append(("stt", enabled))

    async def fake_apply_settings(settings) -> None:
        seen.append(("apply_settings", settings))

    async def fake_apply_providers() -> None:
        seen.append(("apply_providers", True))

    app.controller = SimpleNamespace(
        submit_text=fake_submit,
        set_translation_enabled=fake_translation,
        set_stt_enabled=fake_stt,
        apply_settings=fake_apply_settings,
        apply_providers=fake_apply_providers,
    )

    app._on_manual_submit("You", "hello")
    app._on_translation_toggle(True)
    app._on_stt_toggle(False)
    app._on_settings_changed("settings")
    app._on_providers_changed()

    assert len(app.page.tasks) == 5
    for task_fn in app.page.tasks:
        await task_fn()

    assert seen == [
        ("submit", "hello"),
        ("translation", True),
        ("stt", False),
        ("apply_settings", "settings"),
        ("apply_providers", True),
    ]


@pytest.mark.asyncio
async def test_on_language_change_updates_settings_and_shows_warning(monkeypatch) -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    settings = SimpleNamespace(
        languages=SimpleNamespace(source_language="ko", target_language="en"),
        provider=SimpleNamespace(stt=SimpleNamespace(value="deepgram")),
    )
    seen: list[object] = []

    async def fake_apply_settings(updated) -> None:
        seen.append(updated)

    warning = SimpleNamespace(key="dashboard.warn_stt_key", language_code="ko")
    monkeypatch.setattr(
        app_module, "get_stt_compatibility_warning", lambda *_args, **_kwargs: warning
    )
    app.controller = SimpleNamespace(settings=settings, apply_settings=fake_apply_settings)

    app._on_language_change("ja", "fr")

    assert settings.languages.source_language == "ja"
    assert settings.languages.target_language == "fr"
    assert len(app.page.opened) == 1
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert seen == [settings]


@pytest.mark.asyncio
async def test_on_verify_api_key_persists_and_updates_dashboard_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.view_dashboard = SimpleNamespace(
        stt_calls=[],
        trans_calls=[],
        set_stt_needs_key=lambda value, update_ui=False: app.view_dashboard.stt_calls.append(
            (value, update_ui)
        ),
        set_translation_needs_key=lambda value, update_ui=False: app.view_dashboard.trans_calls.append(
            (value, update_ui)
        ),
    )

    async def fake_verify(provider: str, key: str):
        _ = key
        return provider == "deepgram", "ok"

    settings = SimpleNamespace(
        api_key_verified=SimpleNamespace(
            deepgram=False,
            soniox=False,
            google=False,
            alibaba_beijing=False,
            alibaba_singapore=False,
        )
    )
    app.controller = SimpleNamespace(
        verify_api_key=fake_verify,
        settings=settings,
        config_path="settings.json",
    )

    saves: list[tuple[object, object]] = []
    monkeypatch.setattr(app_module, "save_settings", lambda path, cfg: saves.append((path, cfg)))

    deepgram_result = await app._on_verify_api_key("deepgram", "k")
    google_result = await app._on_verify_api_key("google", "k")

    assert deepgram_result == (True, "ok")
    assert google_result == (False, "ok")
    assert settings.api_key_verified.deepgram is True
    assert settings.api_key_verified.google is False
    assert app.view_dashboard.stt_calls[-1] == (False, False)
    assert app.view_dashboard.trans_calls[-1] == (True, False)
    assert len(saves) == 2


def test_show_snackbar_opens_page_snackbar() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()

    app._show_snackbar("hello", "green", duration=1234)

    assert len(app.page.opened) == 1
    snackbar = app.page.opened[0]
    assert snackbar.duration == 1234


@pytest.mark.asyncio
async def test_check_and_notify_update_handles_none_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DummyPage()

    async def no_update():
        return None

    monkeypatch.setattr(app_module, "check_for_update", no_update)
    await _check_and_notify_update(page)
    assert page.opened == []

    update_info = SimpleNamespace(version="9.9.9", download_url="https://example.com")

    async def has_update():
        return update_info

    monkeypatch.setattr(app_module, "check_for_update", has_update)
    opened_urls: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: opened_urls.append(url))
    await _check_and_notify_update(page)

    assert len(page.opened) == 1
    snackbar = page.opened[0]
    download_btn = snackbar.content.controls[2]
    download_btn.on_click(None)
    assert opened_urls == ["https://example.com"]
    assert page.updated == 1


@pytest.mark.asyncio
async def test_check_and_notify_update_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    page = DummyPage()

    async def raise_error():
        raise RuntimeError("network down")

    monkeypatch.setattr(app_module, "check_for_update", raise_error)
    await _check_and_notify_update(page)
    assert page.opened == []
