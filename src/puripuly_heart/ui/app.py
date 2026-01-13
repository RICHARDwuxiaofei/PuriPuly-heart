import logging
import webbrowser

import flet as ft

from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.updater import check_for_update
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.controller import GuiController
from puripuly_heart.ui.theme import COLOR_BACKGROUND, get_app_theme
from puripuly_heart.ui.views.dashboard import DashboardView
from puripuly_heart.ui.views.history import HistoryView
from puripuly_heart.ui.views.logs import LogsView
from puripuly_heart.ui.views.settings import SettingsView

logger = logging.getLogger(__name__)


class TranslatorApp:
    def __init__(self, page: ft.Page, *, config_path):
        self.page = page
        self.controller = GuiController(page=page, app=self, config_path=config_path)
        self._setup_page()
        self._build_layout()

        # Link Dashboard callbacks
        self.view_dashboard.on_send_message = self._on_manual_submit
        self.view_dashboard.on_toggle_translation = self._on_translation_toggle
        self.view_dashboard.on_toggle_stt = self._on_stt_toggle
        self.view_dashboard.on_language_change = self._on_language_change

        self.view_settings.on_settings_changed = self._on_settings_changed
        self.view_settings.on_providers_changed = self._on_providers_changed
        self.view_settings.on_verify_api_key = self._on_verify_api_key

    def _setup_page(self):
        self.page.title = "PuriPuly Heart"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = get_app_theme()
        self.page.bgcolor = COLOR_BACKGROUND
        self.page.padding = 0
        self.page.window.frameless = True
        self.page.window.resizable = True  # Ensure resizing is allowed
        self.page.window.width = 960
        self.page.window.height = 780  # 16:13 ratio (approx)
        self.page.window.min_width = 800
        self.page.window.min_height = 600

    def _build_layout(self):
        # Initialize Views
        self.view_dashboard = DashboardView()
        self.view_settings = SettingsView()
        self.view_history = HistoryView()
        self.view_logs = LogsView()

        # Custom title bar
        self.title_bar = TitleBar(self.page)

        # Bottom navigation (order: Home, Settings, History, Logs)
        self.bottom_nav = BottomNavBar(on_change=self._on_nav_change)

        # Content area
        self.content_area = ft.Container(
            expand=True,
            padding=16,
            content=self.view_dashboard,
        )

        # Main layout: TitleBar -> Content -> BottomNav
        self.layout = ft.Column(
            controls=[
                self.title_bar,
                self.content_area,
                self.bottom_nav,
            ],
            expand=True,
            spacing=0,
        )

        self.page.add(ft.Container(content=self.layout, expand=True, padding=0))

    def _on_nav_change(self, index: int):
        if index == 0:
            self.content_area.content = self.view_dashboard
        elif index == 1:
            self.content_area.content = self.view_settings
        elif index == 2:
            self.content_area.content = self.view_history
        elif index == 3:
            self.content_area.content = self.view_logs

        self.content_area.update()
        if index == 1:
            self.view_settings.refresh_prompt_if_empty()

    def add_history_entry(self, source: str, text: str):
        self.view_history.add_message(source, text)

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.controller.submit_text(text)

        self.page.run_task(_task)

    def _on_translation_toggle(self, enabled: bool) -> None:
        async def _task():
            await self.controller.set_translation_enabled(enabled)

        self.page.run_task(_task)

    def _on_stt_toggle(self, enabled: bool) -> None:
        async def _task():
            await self.controller.set_stt_enabled(enabled)

        self.page.run_task(_task)

    def _on_language_change(self, source_code: str, target_code: str) -> None:
        if self.controller.settings is None:
            return
        settings = self.controller.settings
        settings.languages.source_language = source_code
        settings.languages.target_language = target_code

        # Check STT provider compatibility and show warning if needed
        stt_provider = settings.provider.stt.value
        warning = get_stt_compatibility_warning(source_code, stt_provider)
        if warning:
            self.page.open(
                ft.SnackBar(
                    ft.Text(warning),
                    bgcolor=ft.Colors.ORANGE_700,
                    duration=4000,
                )
            )

        async def _task():
            await self.controller.apply_settings(settings)

        self.page.run_task(_task)

    def _on_settings_changed(self, settings) -> None:
        async def _task():
            await self.controller.apply_settings(settings)

        self.page.run_task(_task)

    def _on_providers_changed(self) -> None:
        async def _task():
            await self.controller.apply_providers()

        self.page.run_task(_task)

    async def _on_verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        return await self.controller.verify_api_key(provider, key)


async def main_gui(page: ft.Page, *, config_path):
    app = TranslatorApp(page, config_path=config_path)
    await app.controller.start()

    # Check for updates in background
    await _check_and_notify_update(page)


async def _check_and_notify_update(page: ft.Page) -> None:
    """Check for updates and show notification if available."""
    try:
        update_info = await check_for_update()
        if update_info is None:
            return

        def _open_download(_e):
            webbrowser.open(update_info.download_url)
            page.close(banner)

        def _dismiss(_e):
            page.close(banner)

        banner = ft.Banner(
            bgcolor=ft.Colors.BLUE_900,
            leading=ft.Icon(name=ft.Icons.SYSTEM_UPDATE, color=ft.Colors.BLUE_200, size=40),
            content=ft.Text(
                f"New version v{update_info.version} is available!",
                color=ft.Colors.WHITE,
                size=14,
            ),
            actions=[
                ft.TextButton(text="Download", on_click=_open_download),
                ft.TextButton(text="Close", on_click=_dismiss),
            ],
        )
        page.open(banner)

    except Exception as exc:
        logger.debug(f"Update check notification failed: {exc}")
