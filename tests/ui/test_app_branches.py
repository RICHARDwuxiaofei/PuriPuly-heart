from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")
import flet as ft

import puripuly_heart.ui.app as app_module
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.ui.app import TranslatorApp, _check_and_notify_update


class DummyPage:
    def __init__(self) -> None:
        self.opened: list[object] = []
        self.closed: list[object] = []
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
        self.dialog = None

    def open(self, control) -> None:
        self.opened.append(control)

    def close(self, control) -> None:
        self.closed.append(control)
        if self.dialog is control:
            self.dialog = None

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


class RuntimeLoggingController:
    def __init__(self) -> None:
        self.basic_messages: list[str] = []
        self.detailed_messages: list[str] = []

    def log_basic(self, message: str, *, level: int = app_module.logging.INFO) -> None:
        _ = level
        self.basic_messages.append(message)

    def log_detailed(self, message: str, *, level: int = app_module.logging.INFO) -> None:
        _ = level
        self.detailed_messages.append(message)


def test_translator_app_init_builds_layout_and_wires_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyController:
        def __init__(self, page, app, config_path):
            self.page = page
            self.app = app
            self.config_path = config_path
            self.settings = None
            self.runtime_logging_mode = "detailed"
            self.basic_messages: list[str] = []
            self.detailed_messages: list[str] = []

        def set_runtime_logging_mode(self, mode: str) -> None:
            self.runtime_logging_mode = mode

        def log_basic(self, message: str, *, level: int = app_module.logging.INFO) -> None:
            _ = level
            self.basic_messages.append(message)

        def log_detailed(self, message: str, *, level: int = app_module.logging.INFO) -> None:
            _ = level
            self.detailed_messages.append(message)

    class DummyDashboardView(ft.Container):
        def __init__(self) -> None:
            super().__init__()
            self.on_send_message = None
            self.on_toggle_translation = None
            self.on_toggle_stt = None
            self.on_toggle_overlay = None
            self.on_toggle_peer_translation = None
            self.on_language_change = None
            self.overlay_peer_contract = None

        def set_overlay_peer_contract(self, contract) -> None:
            self.overlay_peer_contract = contract

        def apply_locale(self) -> None:
            return None

    class DummySettingsView(ft.Container):
        def __init__(self) -> None:
            super().__init__()
            self.on_settings_changed = None
            self.on_prompt_apply_settings = None
            self.on_providers_changed = None
            self.on_verify_api_key = None
            self.on_secret_cleared = None
            self.show_snackbar = None
            self.overlay_peer_contract = None

        def set_overlay_runtime_state(self, *_args, **_kwargs) -> None:
            return None

        def set_overlay_peer_contract(self, contract) -> None:
            self.overlay_peer_contract = contract

        def apply_locale(self) -> None:
            return None

    class DummyLogsView(ft.Container):
        def __init__(self) -> None:
            super().__init__()
            self.on_mode_change = None
            self.runtime_logging_mode = "basic"

        def set_runtime_logging_mode(self, mode: str) -> None:
            self.runtime_logging_mode = mode

        def apply_locale(self) -> None:
            return None

        async def scroll_to_bottom(self) -> None:
            return None

        def log_basic(self, message: str, *, level: int = app_module.logging.INFO) -> None:
            _ = (message, level)

        def log_detailed(self, message: str, *, level: int = app_module.logging.INFO) -> None:
            _ = (message, level)

    monkeypatch.setattr(app_module, "GuiController", DummyController)
    monkeypatch.setattr(app_module, "DashboardView", DummyDashboardView)
    monkeypatch.setattr(app_module, "SettingsView", DummySettingsView)
    monkeypatch.setattr(app_module, "LogsView", DummyLogsView)
    monkeypatch.setattr(app_module, "AboutView", lambda: ft.Container())
    monkeypatch.setattr(app_module, "TitleBar", lambda _page: ft.Container())
    monkeypatch.setattr(app_module, "BottomNavBar", lambda on_change: ft.Container(data=on_change))
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
    assert page.window.width == 1200
    assert page.window.height == 800
    assert page.window.min_width == 1080
    assert page.window.min_height == 600
    assert page.added
    assert app.view_dashboard.on_send_message == app._on_manual_submit
    assert app.view_dashboard.on_toggle_overlay == app._on_overlay_toggle
    assert app.view_dashboard.on_toggle_peer_translation == app._on_peer_translation_toggle
    assert app.view_settings.on_verify_api_key == app._on_verify_api_key
    assert app.view_settings.on_prompt_apply_settings == app._on_prompt_apply_settings
    assert not hasattr(app.view_settings, "on_overlay_toggle")
    assert not hasattr(app.view_settings, "on_peer_translation_toggle")
    assert app.view_settings.runtime_log_basic == app.controller.log_basic
    assert app.view_settings.runtime_log_detailed == app.controller.log_detailed
    assert app.view_logs.on_mode_change == app._on_runtime_logging_mode_change
    assert app.view_logs.runtime_logging_mode == "detailed"


def test_on_runtime_logging_mode_change_updates_controller_and_logs_view() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    seen: list[str] = []

    def fake_set_mode(mode: str) -> None:
        seen.append(mode)
        app.controller.runtime_logging_mode = mode

    app.controller = SimpleNamespace(
        runtime_logging_mode="basic",
        set_runtime_logging_mode=fake_set_mode,
    )
    app.view_logs = SimpleNamespace(
        set_runtime_logging_mode=lambda mode: seen.append(f"view:{mode}")
    )

    app._on_runtime_logging_mode_change("detailed")

    assert seen == ["detailed", "view:detailed"]


@pytest.mark.asyncio
async def test_main_gui_routes_update_check_through_app_log_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DummyPage()
    seen: dict[str, object] = {}

    class FakeController:
        async def start(self) -> None:
            seen["started"] = True

    class FakeApp:
        def __init__(self, incoming_page, *, config_path):
            seen["init"] = (incoming_page, config_path)
            seen["app"] = self
            self.page = incoming_page
            self.controller = FakeController()

        def _log_detailed(self, message: str, *, level: int = app_module.logging.INFO) -> None:
            _ = (message, level)

    async def fake_check_and_notify_update(incoming_page, *, log_detailed=None) -> None:
        seen["check"] = (incoming_page, log_detailed)

    monkeypatch.setattr(app_module, "TranslatorApp", FakeApp)
    monkeypatch.setattr(app_module, "_check_and_notify_update", fake_check_and_notify_update)

    await app_module.main_gui(page, config_path=Path("settings.json"))

    assert seen["started"] is True
    assert seen["check"][0] is page
    assert getattr(seen["check"][1], "__self__", None) is seen["app"]
    assert getattr(seen["check"][1], "__func__", None) is FakeApp._log_detailed


@pytest.mark.asyncio
async def test_on_nav_change_merges_current_languages_into_prompt_only_apply() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app._current_tab = 1
    app.view_dashboard = object()
    app.view_logs = SimpleNamespace(scroll_to_bottom=lambda: asyncio.sleep(0))
    app.view_about = object()
    pending_settings = object()
    merged_settings = object()
    app.view_settings = SimpleNamespace(
        has_provider_changes=False,
        has_pending_prompt_changes=True,
        consume_prompt_apply_settings=lambda: pending_settings,
        refresh_prompt_if_empty=lambda: None,
    )
    app.content_area = DummyContent()
    events: list[tuple[str, object]] = []

    def fake_merge_settings(settings) -> object:
        events.append(("merge", settings))
        return merged_settings

    async def fake_apply_settings(settings) -> None:
        events.append(("apply", settings))

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
        apply_providers=lambda _settings=None: asyncio.sleep(0),
    )

    app._on_nav_change(0)

    assert app.content_area.content is app.view_dashboard
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert events == [("merge", pending_settings), ("apply", merged_settings)]


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
        consume_provider_apply_settings=lambda: "merged-settings",
        refresh_prompt_if_empty=lambda: None,
    )
    app.content_area = DummyContent()
    seen: list[object] = []

    async def fake_apply_providers(settings) -> None:
        seen.append(settings)

    app.controller = SimpleNamespace(apply_providers=fake_apply_providers)

    app._on_nav_change(0)
    assert app.content_area.content is app.view_dashboard
    assert app.view_settings.has_provider_changes is False
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert seen == ["merged-settings"]


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
        refresh_prompt_if_empty=lambda: refreshed.__setitem__("count", refreshed["count"] + 1),
    )
    app.view_logs = SimpleNamespace(scroll_to_bottom=fake_scroll_to_bottom)
    app.view_about = object()
    app.content_area = DummyContent()
    app.controller = SimpleNamespace(apply_providers=lambda _settings=None: asyncio.sleep(0))

    app._on_nav_change(1)
    assert app.content_area.content is app.view_settings
    assert refreshed["count"] == 1

    app._on_nav_change(2)
    assert app.content_area.content is app.view_logs
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert scrolled["count"] == 1


@pytest.mark.asyncio
async def test_on_nav_change_applies_pending_prompt_changes_when_leaving_settings() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app._current_tab = 1
    app.view_dashboard = object()
    app.view_logs = SimpleNamespace(scroll_to_bottom=lambda: asyncio.sleep(0))
    app.view_about = object()
    pending_settings = object()
    merged_settings = object()
    merge_calls: list[object] = []
    app.view_settings = SimpleNamespace(
        has_provider_changes=False,
        has_pending_prompt_changes=True,
        consume_prompt_apply_settings=lambda: pending_settings,
        refresh_prompt_if_empty=lambda: None,
    )
    app.content_area = DummyContent()
    seen: list[object] = []

    def fake_merge_settings(settings) -> object:
        merge_calls.append(settings)
        return merged_settings

    async def fake_apply_settings(settings) -> None:
        seen.append(settings)

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
        apply_providers=lambda _settings=None: asyncio.sleep(0),
    )

    app._on_nav_change(0)

    assert app.content_area.content is app.view_dashboard
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert merge_calls == [pending_settings]
    assert seen == [merged_settings]


@pytest.mark.asyncio
async def test_on_prompt_apply_settings_merges_current_languages_before_apply_settings() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    pending_settings = object()
    merged_settings = object()
    events: list[tuple[str, object]] = []

    def fake_merge_settings(settings) -> object:
        events.append(("merge", settings))
        return merged_settings

    async def fake_apply_settings(settings) -> None:
        events.append(("apply", settings))

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
    )

    app._on_prompt_apply_settings(pending_settings)

    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert events == [("merge", pending_settings), ("apply", merged_settings)]


@pytest.mark.asyncio
async def test_prompt_apply_keeps_dashboard_target_for_next_request() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    pending_settings = AppSettings()
    pending_settings.languages.target_language = "en"
    merged_settings = AppSettings()
    merged_settings.languages.target_language = "ja"
    applied_targets: list[str] = []

    def fake_merge_settings(settings: AppSettings) -> AppSettings:
        assert settings is pending_settings
        return merged_settings

    async def fake_apply_settings(settings: AppSettings) -> None:
        applied_targets.append(settings.languages.target_language)

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
    )

    app._on_prompt_apply_settings(pending_settings)

    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert pending_settings.languages.target_language == "en"
    assert applied_targets == ["ja"]


@pytest.mark.asyncio
async def test_on_settings_changed_applies_raw_settings_without_prompt_merge() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    raw_settings = object()
    seen: list[object] = []

    def fake_merge_settings(_settings) -> object:
        raise AssertionError("prompt merge should not run for generic settings changes")

    async def fake_apply_settings(settings) -> None:
        seen.append(settings)

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
    )

    app._on_settings_changed(raw_settings)

    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert seen == [raw_settings]


@pytest.mark.asyncio
async def test_queue_orders_generic_settings_change_before_prompt_apply() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    raw_settings = object()
    pending_settings = object()
    merged_settings = object()
    events: list[tuple[str, object]] = []

    def fake_merge_settings(settings) -> object:
        events.append(("merge", settings))
        return merged_settings

    async def fake_apply_settings(settings) -> None:
        events.append(("apply", settings))

    app.controller = SimpleNamespace(
        merge_settings_tab_apply_with_current_languages=fake_merge_settings,
        apply_settings=fake_apply_settings,
    )

    app._on_settings_changed(raw_settings)
    app._on_prompt_apply_settings(pending_settings)

    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()

    assert events == [
        ("apply", raw_settings),
        ("merge", pending_settings),
        ("apply", merged_settings),
    ]


@pytest.mark.asyncio
async def test_queue_orders_generic_settings_change_before_provider_apply_on_settings_exit() -> (
    None
):
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app._current_tab = 1
    raw_settings = object()
    provider_settings = object()
    app.view_dashboard = object()
    app.view_logs = SimpleNamespace(scroll_to_bottom=lambda: asyncio.sleep(0))
    app.view_about = object()
    app.view_settings = SimpleNamespace(
        has_provider_changes=True,
        consume_provider_apply_settings=lambda: provider_settings,
        refresh_prompt_if_empty=lambda: None,
    )
    app.content_area = DummyContent()
    events: list[tuple[str, object]] = []

    async def fake_apply_settings(settings) -> None:
        events.append(("settings", settings))

    async def fake_apply_providers(settings) -> None:
        events.append(("providers", settings))

    app.controller = SimpleNamespace(
        apply_settings=fake_apply_settings,
        apply_providers=fake_apply_providers,
    )

    app._on_settings_changed(raw_settings)
    app._on_nav_change(0)

    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()

    assert events == [("settings", raw_settings), ("providers", provider_settings)]


def test_on_nav_change_closes_open_dialog_before_switching_tabs() -> None:
    events: list[tuple[str, object]] = []

    class RecordingContent:
        def __init__(self, initial) -> None:
            self._content = initial

        @property
        def content(self):
            return self._content

        @content.setter
        def content(self, value) -> None:
            events.append(("content", value))
            self._content = value

        def update(self) -> None:
            events.append(("update", self._content))

    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    dialog = object()
    app.page.dialog = dialog

    def fake_close(control) -> None:
        events.append(("close", control))
        app.page.closed.append(control)
        if app.page.dialog is control:
            app.page.dialog = None

    app.page.close = fake_close
    app._current_tab = 0
    app.view_dashboard = object()
    app.view_settings = SimpleNamespace(
        has_provider_changes=False,
        refresh_prompt_if_empty=lambda: None,
    )
    app.view_logs = SimpleNamespace(scroll_to_bottom=lambda: asyncio.sleep(0))
    app.view_about = object()
    app.content_area = RecordingContent(app.view_dashboard)
    app.controller = SimpleNamespace(apply_providers=lambda _settings=None: asyncio.sleep(0))

    app._on_nav_change(1)

    assert events[:3] == [
        ("close", dialog),
        ("content", app.view_settings),
        ("update", app.view_settings),
    ]
    assert app.page.closed == [dialog]


def test_apply_locale_updates_views_and_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app.title_bar = SimpleNamespace(set_title=lambda value: setattr(app, "_title", value))
    view_calls: list[str] = []
    app.view_dashboard = SimpleNamespace(apply_locale=lambda: view_calls.append("dash"))
    app.view_settings = SimpleNamespace(apply_locale=lambda: view_calls.append("settings"))
    app.view_logs = SimpleNamespace(apply_locale=lambda: view_calls.append("logs"))
    monkeypatch.setattr(app_module, "get_app_theme", lambda **_kwargs: "theme")
    monkeypatch.setattr(app_module, "font_for_language", lambda _code: "font")
    monkeypatch.setattr(app_module, "get_locale", lambda: "en")

    app.apply_locale()

    assert app.page.title == app_module.t("app.title")
    assert view_calls == ["dash", "settings", "logs"]
    assert app.page.updated == 1


def test_refresh_overlay_peer_contract_ignores_missing_controller() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.view_dashboard = SimpleNamespace(
        set_overlay_peer_contract=lambda contract: (_ for _ in ()).throw(
            AssertionError(f"unexpected dashboard contract: {contract}")
        )
    )
    app.view_settings = SimpleNamespace(
        set_overlay_peer_contract=lambda contract: (_ for _ in ()).throw(
            AssertionError(f"unexpected settings contract: {contract}")
        )
    )

    app.refresh_overlay_peer_contract()

    assert getattr(app, "overlay_peer_contract", None) is None


def test_on_overlay_state_changed_updates_settings_view_runtime_state() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    contract = object()
    seen: list[tuple[str, str | None]] = []
    refreshed: list[object] = []
    app.controller = SimpleNamespace(build_overlay_peer_consumer_contract=lambda: contract)
    app.view_dashboard = SimpleNamespace(
        set_overlay_peer_contract=lambda incoming: refreshed.append(("dashboard", incoming))
    )
    app.view_settings = SimpleNamespace(
        set_overlay_runtime_state=lambda state, failure_reason=None: seen.append(
            (state, failure_reason)
        ),
        set_overlay_peer_contract=lambda incoming: refreshed.append(("settings", incoming)),
    )

    app.on_overlay_state_changed(state="failed", failure_reason="runtime_crashed")

    assert app.overlay_state == "failed"
    assert app.overlay_failure_reason == "runtime_crashed"
    assert seen == [("failed", "runtime_crashed")]
    assert refreshed == [("settings", contract), ("dashboard", contract)]


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

    async def fake_overlay(enabled: bool) -> None:
        seen.append(("overlay", enabled))

    async def fake_peer(enabled: bool) -> None:
        seen.append(("peer", enabled))

    async def fake_apply_settings(settings) -> None:
        seen.append(("apply_settings", settings))

    async def fake_apply_providers() -> None:
        seen.append(("apply_providers", True))

    app.controller = SimpleNamespace(
        submit_text=fake_submit,
        set_translation_enabled=fake_translation,
        set_stt_enabled=fake_stt,
        set_overlay_enabled=fake_overlay,
        set_peer_translation_enabled=fake_peer,
        apply_settings=fake_apply_settings,
        apply_providers=fake_apply_providers,
    )

    app._on_manual_submit("You", "hello")
    app._on_translation_toggle(True)
    app._on_stt_toggle(False)
    app._on_overlay_toggle(True)
    app._on_peer_translation_toggle(True)
    app._on_settings_changed("settings")
    app._on_providers_changed()

    assert len(app.page.tasks) == 6
    for task_fn in app.page.tasks:
        await task_fn()

    assert seen == [
        ("submit", "hello"),
        ("translation", True),
        ("stt", False),
        ("overlay", True),
        ("peer", True),
        ("apply_settings", "settings"),
        ("apply_providers", True),
    ]


def test_toggle_handlers_route_basic_and_detailed_runtime_logs() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app.overlay_state = "connected"
    app.overlay_failure_reason = "runtime_crashed"
    app.view_dashboard = SimpleNamespace(is_translation_on=False, is_stt_on=True)

    controller = RuntimeLoggingController()

    async def fake_translation(enabled: bool) -> None:
        _ = enabled

    async def fake_stt(enabled: bool) -> None:
        _ = enabled

    async def fake_overlay(enabled: bool) -> None:
        _ = enabled

    controller.set_translation_enabled = fake_translation
    controller.set_stt_enabled = fake_stt
    controller.set_overlay_enabled = fake_overlay
    app.controller = controller

    app._on_translation_toggle(True)
    app._on_stt_toggle(False)
    app._on_overlay_toggle(True)

    assert app.controller.basic_messages == [
        "[Dashboard] Translation toggle requested: enabled=True",
        "[Dashboard] STT toggle requested: enabled=False",
        "[Dashboard] Overlay toggle requested: enabled=True",
    ]
    assert app.controller.detailed_messages == [
        "[Dashboard] Translation toggle detail: dashboard_state=False overlay_state=connected",
        "[Dashboard] STT toggle detail: dashboard_state=True overlay_state=connected",
        "[Dashboard] Overlay toggle detail: overlay_state=connected failure_reason=runtime_crashed",
    ]


def test_on_overlay_state_changed_routes_runtime_logs() -> None:
    controller = RuntimeLoggingController()
    app = TranslatorApp.__new__(TranslatorApp)
    app.controller = controller
    seen: list[tuple[str, str | None]] = []
    app.overlay_state = "off"
    app.view_settings = SimpleNamespace(
        set_overlay_runtime_state=lambda state, failure_reason=None: seen.append(
            (state, failure_reason)
        )
    )

    app.on_overlay_state_changed(state="failed", failure_reason="runtime_crashed")

    assert controller.basic_messages == ["[Overlay] State changed: off -> failed"]
    assert controller.detailed_messages == [
        "[Overlay] State detail: overlay_state=failed failure_reason=runtime_crashed"
    ]
    assert seen == [("failed", "runtime_crashed")]


@pytest.mark.asyncio
async def test_on_language_change_updates_settings_and_shows_warning(monkeypatch) -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    settings = SimpleNamespace(
        languages=SimpleNamespace(source_language="ko", target_language="en"),
        provider=SimpleNamespace(stt=SimpleNamespace(value="deepgram")),
    )
    seen: list[tuple[str, str, str, str]] = []

    async def fake_on_dashboard_language_change(
        *, source_code: str, target_code: str, peer_source_code: str, peer_target_code: str
    ) -> None:
        seen.append((source_code, target_code, peer_source_code, peer_target_code))

    warning = SimpleNamespace(key="dashboard.warn_stt_key", language_code="ko")
    monkeypatch.setattr(
        app_module, "get_stt_compatibility_warning", lambda *_args, **_kwargs: warning
    )
    app.controller = SimpleNamespace(
        settings=settings,
        on_dashboard_language_change=fake_on_dashboard_language_change,
    )

    app._on_language_change("ja", "fr", "", "it")

    assert settings.languages.source_language == "ko"
    assert settings.languages.target_language == "en"
    assert len(app.page.opened) == 1
    assert len(app.page.tasks) == 1
    await app.page.tasks[0]()
    assert seen == [("ja", "fr", "", "it")]


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
            openrouter=False,
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
    openrouter_result = await app._on_verify_api_key("openrouter", "k")

    assert deepgram_result == (True, "ok")
    assert google_result == (False, "ok")
    assert openrouter_result == (False, "ok")
    assert settings.api_key_verified.deepgram is True
    assert settings.api_key_verified.google is False
    assert settings.api_key_verified.openrouter is False
    assert app.view_dashboard.stt_calls[-1] == (False, False)
    assert app.view_dashboard.trans_calls[-1] == (True, False)
    assert len(saves) == 3


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
    monkeypatch.setattr(
        app_module.ft, "Icon", lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs)
    )
    monkeypatch.setattr(
        app_module.ft,
        "TextButton",
        lambda *args, **kwargs: SimpleNamespace(on_click=kwargs.get("on_click")),
    )
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
    app = TranslatorApp.__new__(TranslatorApp)
    app.controller = RuntimeLoggingController()

    async def raise_error():
        raise RuntimeError("network down")

    monkeypatch.setattr(app_module, "check_for_update", raise_error)
    await _check_and_notify_update(page, log_detailed=app._log_detailed)
    assert page.opened == []
    assert app.controller.basic_messages == []
    assert app.controller.detailed_messages == ["[Update] Check notification failed: network down"]
