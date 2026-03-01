import logging
import webbrowser

import flet as ft

from puripuly_heart.config.settings import save_settings
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.updater import check_for_update
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.controller import GuiController
from puripuly_heart.ui.fonts import font_for_language, register_fonts
from puripuly_heart.ui.i18n import (
    get_locale,
    language_name,
    t,
)
from puripuly_heart.ui.theme import (
    COLOR_BACKGROUND,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    get_app_theme,
)
from puripuly_heart.ui.views.about import AboutView
from puripuly_heart.ui.views.dashboard import DashboardView
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
        self.view_settings.on_secret_cleared = self._on_secret_cleared
        self.view_settings.show_snackbar = self._show_snackbar

    def _setup_page(self):
        self.page.title = t("app.title")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        register_fonts(self.page)
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.page.bgcolor = COLOR_BACKGROUND
        self.page.padding = 0
        self.page.window.frameless = True
        self.page.window.resizable = True  # Ensure resizing is allowed
        self.page.window.width = 960
        self.page.window.height = 780  # 16:13 ratio (approx)
        self.page.window.min_width = 800
        self.page.window.min_height = 600
        self.page.window.icon = "icons/icon.ico"

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        self.view_settings = SettingsView()
        self.view_logs = LogsView()
        self.view_about = AboutView()

        # Custom title bar
        self.title_bar = TitleBar(self.page)

        # Bottom navigation (order: Home, Settings, Logs, About)
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
        # Track previous tab for Settings auto-apply
        previous_tab = getattr(self, "_current_tab", 0)
        self._current_tab = index

        # Auto-apply provider changes when leaving Settings (tab 1)
        if previous_tab == 1 and index != 1 and self.view_settings.has_provider_changes:
            rebuild_stt = self.view_settings.provider_change_requires_pipeline
            self.view_settings.has_provider_changes = False
            self.view_settings.provider_change_requires_pipeline = False

            async def _task():
                await self.controller.apply_providers(rebuild_stt=rebuild_stt)

            self.page.run_task(_task)

        if index == 0:
            self.content_area.content = self.view_dashboard
        elif index == 1:
            self.content_area.content = self.view_settings
        elif index == 2:
            self.content_area.content = self.view_logs
        elif index == 3:
            self.content_area.content = self.view_about

        self.content_area.update()
        if index == 1:
            self.view_settings.refresh_prompt_if_empty()
        elif index == 2:
            # Async scroll after rendering completes
            async def _scroll():
                import asyncio

                await asyncio.sleep(0.05)
                await self.view_logs.scroll_to_bottom()

            self.page.run_task(_scroll)

    def apply_locale(self) -> None:
        self.page.title = t("app.title")
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.title_bar.set_title(t("app.title"))
        self.view_dashboard.apply_locale()
        self.view_settings.apply_locale()
        self.view_logs.apply_locale()
        self.page.update()

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
                    ft.Text(t(warning.key, language=language_name(warning.language_code))),
                    bgcolor=ft.Colors.ORANGE_700,
                    duration=4000,
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.margin.only(bottom=90),
                    padding=20,
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
        success, msg = await self.controller.verify_api_key(provider, key)

        # Save verification result to settings
        setattr(self.controller.settings.api_key_verified, provider, success)
        save_settings(self.controller.config_path, self.controller.settings)

        # Sync verification result with dashboard needs_key flags (UI update on user click)
        if provider in ("deepgram", "soniox", "qwen_asr"):
            self.view_dashboard.set_stt_needs_key(not success, update_ui=False)
        elif provider in ("google", "alibaba_beijing", "alibaba_singapore"):
            self.view_dashboard.set_translation_needs_key(not success, update_ui=False)

        return success, msg

    def _on_secret_cleared(self, key: str) -> None:
        """Reset verification status when API key is cleared."""
        # Map secret key name to provider name
        key_to_provider = {
            "deepgram_api_key": "deepgram",
            "soniox_api_key": "soniox",
            "google_api_key": "google",
            "alibaba_api_key": "alibaba_beijing",  # Use beijing as default
            "alibaba_api_key_beijing": "alibaba_beijing",
            "alibaba_api_key_singapore": "alibaba_singapore",
        }
        provider = key_to_provider.get(key)
        if provider:
            setattr(self.controller.settings.api_key_verified, provider, False)
            save_settings(self.controller.config_path, self.controller.settings)

            # Update dashboard needs_key flag
            if provider in ("deepgram", "soniox"):
                self.view_dashboard.set_stt_needs_key(True, update_ui=False)
            elif provider in ("google", "alibaba_beijing", "alibaba_singapore"):
                self.view_dashboard.set_translation_needs_key(True, update_ui=False)

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        """Show a snackbar above the bottom nav."""
        self.page.open(
            ft.SnackBar(
                ft.Text(message, size=18, color=ft.Colors.WHITE),
                bgcolor=bgcolor,
                duration=duration,
                behavior=ft.SnackBarBehavior.FLOATING,
                margin=ft.margin.only(bottom=90),
                padding=20,
            )
        )


async def main_gui(page: ft.Page, *, config_path):
    app = TranslatorApp(page, config_path=config_path)
    await app.controller.start()

    # Check for updates in background
    await _check_and_notify_update(page)


async def _check_and_notify_update(page: ft.Page) -> None:
    """Check for updates and show notification as a toast."""
    try:
        update_info = await check_for_update()
        if update_info is None:
            return

        def _open_download(_e):
            webbrowser.open(update_info.download_url)
            snackbar.open = False
            page.update()

        snackbar = ft.SnackBar(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        name=ft.Icons.SYSTEM_UPDATE,
                        color=ft.Colors.WHITE,
                        size=28,
                    ),
                    ft.Text(
                        t("update.available", version=update_info.version),
                        color=ft.Colors.WHITE,
                        size=18,
                        font_family=font_for_language(get_locale()),
                        expand=True,
                    ),
                    ft.TextButton(
                        text=t("update.download"),
                        on_click=_open_download,
                        style=ft.ButtonStyle(
                            color=ft.Colors.WHITE,
                            text_style=ft.TextStyle(
                                size=18,
                                font_family=font_for_language(get_locale()),
                            ),
                            overlay_color=COLOR_PRIMARY,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            bgcolor=COLOR_SUCCESS,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
            duration=30000,  # 30초
            show_close_icon=True,
            close_icon_color=ft.Colors.WHITE,
        )
        page.open(snackbar)

    except Exception as exc:
        logger.debug(f"Update check notification failed: {exc}")
