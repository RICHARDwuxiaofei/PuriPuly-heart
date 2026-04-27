import copy
import logging
import webbrowser

import flet as ft

from puripuly_heart.config.llm_profiles import (
    get_openrouter_selection_alias_for_model_and_source,
    profile_for_alias,
)
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterSelectionAlias,
    save_settings,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.updater import check_for_update
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog
from puripuly_heart.ui.components.peer_translation_eula_dialog import PeerTranslationEulaDialog
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

DEFAULT_WINDOW_WIDTH = 1136
DEFAULT_WINDOW_HEIGHT = 850
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 760
APP_CONTENT_PADDING = 16
FOUNDER_CONTACT_URL = "https://x.com/kapitalismho"


class TranslatorApp:
    def __init__(self, page: ft.Page, *, config_path, debug_ui_preview: bool = False):
        self.page = page
        self.controller = GuiController(page=page, app=self, config_path=config_path)
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.overlay_peer_contract = None
        self.debug_ui_preview = bool(debug_ui_preview)
        self.debug_preview_panel: DebugPreviewPanel | None = None
        self._openrouter_pkce_request_active = False
        self._setup_page()
        self._build_layout()

        # Link Dashboard callbacks
        self.view_dashboard.on_send_message = self._on_manual_submit
        self.view_dashboard.on_toggle_translation = self._on_translation_toggle
        self.view_dashboard.on_toggle_stt = self._on_stt_toggle
        self.view_dashboard.on_toggle_overlay = self._on_overlay_toggle
        self.view_dashboard.on_toggle_peer_translation = self._on_peer_translation_toggle
        self.view_dashboard.on_language_change = self._on_language_change

        self.view_settings.on_settings_changed = self._on_settings_changed
        self.view_settings.on_prompt_apply_settings = self._on_prompt_apply_settings
        self.view_settings.on_providers_changed = self._on_providers_changed
        self.view_settings.on_request_openrouter_pkce = self._on_request_openrouter_pkce
        self.view_settings.on_verify_api_key = self._on_verify_api_key
        self.view_settings.on_secret_cleared = self._on_secret_cleared
        self.view_settings.show_snackbar = self._show_snackbar
        self.view_logs.on_mode_change = self._on_runtime_logging_mode_change
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)
        runtime_log_basic = getattr(self.controller, "log_basic", None)
        runtime_log_detailed = getattr(self.controller, "log_detailed", None)
        if callable(runtime_log_basic):
            self.view_settings.runtime_log_basic = runtime_log_basic
        if callable(runtime_log_detailed):
            self.view_settings.runtime_log_detailed = runtime_log_detailed
        self.view_dashboard.runtime_log_detailed = self._log_detailed

        calibration_begin = getattr(self.controller, "begin_overlay_calibration", None)
        calibration_change = getattr(self.controller, "set_overlay_calibration_field", None)
        calibration_apply = getattr(self.controller, "apply_overlay_calibration", None)
        calibration_cancel = getattr(self.controller, "cancel_overlay_calibration", None)
        if callable(calibration_begin):
            self.view_settings.on_overlay_calibration_begin = calibration_begin
        if callable(calibration_change):
            self.view_settings.on_overlay_calibration_change = calibration_change
        if callable(calibration_apply):
            self.view_settings.on_overlay_calibration_apply = calibration_apply
        if callable(calibration_cancel):
            self.view_settings.on_overlay_calibration_cancel = calibration_cancel

        set_overlay_calibration = getattr(self.view_settings, "set_overlay_calibration", None)
        overlay_calibration = getattr(self.controller, "overlay_calibration", None)
        if callable(set_overlay_calibration) and overlay_calibration is not None:
            set_overlay_calibration(overlay_calibration)

    def _setup_page(self):
        self.page.title = t("app.title")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        register_fonts(self.page)
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.page.bgcolor = COLOR_BACKGROUND
        self.page.padding = 0
        self.page.window.frameless = True
        self.page.window.resizable = True  # Ensure resizing is allowed
        self.page.window.width = DEFAULT_WINDOW_WIDTH
        self.page.window.height = DEFAULT_WINDOW_HEIGHT
        self.page.window.min_width = MIN_WINDOW_WIDTH
        self.page.window.min_height = MIN_WINDOW_HEIGHT
        self.page.window.icon = "icons/icon.ico"

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        self.view_settings = SettingsView()
        self.view_logs = LogsView()
        self.view_about = AboutView()
        self.view_settings.set_overlay_runtime_state(self.overlay_state)

        # Custom title bar
        self.title_bar = TitleBar(self.page)

        # Bottom navigation (order: Home, Settings, Logs, About)
        self.bottom_nav = BottomNavBar(on_change=self._on_nav_change)

        # Content area
        self.content_area = ft.Container(
            expand=True,
            padding=APP_CONTENT_PADDING,
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

        root_content = ft.Container(content=self.layout, expand=True, padding=0)
        if self.debug_ui_preview:
            self.debug_preview_panel = self._build_debug_preview_panel()
            self.page.add(
                ft.Container(
                    content=ft.Stack(
                        controls=[root_content, self.debug_preview_panel],
                        fit=ft.StackFit.EXPAND,
                        expand=True,
                    ),
                    expand=True,
                    padding=0,
                )
            )
        else:
            self.page.add(root_content)

    def _build_debug_preview_panel(self) -> DebugPreviewPanel:
        return DebugPreviewPanel(
            on_brake_notice=self._preview_brake_notice,
            on_revoked_notice=self._preview_revoked_notice,
            on_founder_letter=self._preview_founder_letter,
            on_pkce_failure=self._preview_pkce_failure,
            on_peer_translation_eula=self._preview_peer_translation_eula,
        )

    def _preview_brake_notice(self) -> None:
        self._show_snackbar(t("managed_release.brake"), ft.Colors.ORANGE_700)

    def _preview_revoked_notice(self) -> None:
        self._show_snackbar(t("managed_release.revoked_contact"), ft.Colors.ORANGE_700)

    def _debug_preview_noop(self) -> None:
        return None

    def _preview_founder_letter(self) -> None:
        dialog = FounderLetterDialog(
            self.page,
            on_connect=self._debug_preview_noop,
            on_contact=self._debug_preview_noop,
        )
        self._founder_letter_dialog = dialog
        dialog.open()

    def _preview_pkce_failure(self) -> None:
        self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)

    def _preview_peer_translation_eula(self) -> None:
        self._show_peer_translation_eula(self._debug_preview_noop)

    def _show_peer_translation_eula(self, on_accept) -> None:
        dialog = PeerTranslationEulaDialog(
            self.page,
            on_accept=on_accept,
            on_cancel=self._debug_preview_noop,
        )
        self._peer_translation_eula_dialog = dialog
        dialog.open()

    def _accept_peer_translation_eula_and_enable(self) -> None:
        async def _task():
            settings = getattr(self.controller, "settings", None)
            if settings is not None:
                settings.ui.peer_translation_eula_accepted = True
                config_path = getattr(self.controller, "config_path", None)
                if config_path is not None:
                    save_settings(config_path, settings)
            await self.controller.set_peer_translation_enabled(True)

        self.page.run_task(_task)

    def _close_open_dialog_for_navigation(self) -> None:
        dialog = getattr(self.page, "dialog", None)
        close_dialog = getattr(self.page, "close", None)
        if dialog is None or not callable(close_dialog):
            return
        try:
            close_dialog(dialog)
        except Exception:
            logger.exception("Failed to close dialog during navigation")

    def _queue_settings_mutation_task(self, task_factory) -> None:
        queue = getattr(self, "_settings_mutation_queue", None)
        if queue is None:
            queue = []
            self._settings_mutation_queue = queue
        queue.append(task_factory)
        if getattr(self, "_settings_mutation_worker_active", False):
            return
        self._settings_mutation_worker_active = True

        async def _worker():
            try:
                while self._settings_mutation_queue:
                    next_task = self._settings_mutation_queue.pop(0)
                    try:
                        await next_task()
                    except Exception:
                        logger.exception("Settings mutation task failed")
            finally:
                self._settings_mutation_worker_active = False

        self.page.run_task(_worker)

    def _content_padding_for_index(self, index: int) -> int:
        return 0 if index == 1 else APP_CONTENT_PADDING

    def _on_nav_change(self, index: int):
        # Track previous tab for Settings auto-apply
        previous_tab = getattr(self, "_current_tab", 0)
        if previous_tab != index:
            self._close_open_dialog_for_navigation()
        self._current_tab = index

        # Auto-apply Settings changes when leaving Settings (tab 1)
        if previous_tab == 1 and index != 1:
            if self.view_settings.has_provider_changes:
                pending_settings = self.view_settings.consume_provider_apply_settings()
                if pending_settings is not None:
                    self.view_settings.has_provider_changes = False

                    async def _task():
                        await self.controller.apply_providers(pending_settings)

                    self._queue_settings_mutation_task(_task)
            elif getattr(self.view_settings, "has_pending_prompt_changes", False):
                pending_settings = self.view_settings.consume_prompt_apply_settings()
                if pending_settings is not None:

                    async def _task():
                        merged_settings = (
                            self.controller.merge_settings_tab_apply_with_current_languages(
                                pending_settings
                            )
                        )
                        await self.controller.apply_settings(merged_settings)

                    self._queue_settings_mutation_task(_task)

        if index == 0:
            self.content_area.content = self.view_dashboard
        elif index == 1:
            self.content_area.content = self.view_settings
        elif index == 2:
            self.content_area.content = self.view_logs
        elif index == 3:
            self.content_area.content = self.view_about

        self.content_area.padding = self._content_padding_for_index(index)
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
        self.refresh_overlay_peer_contract()
        self.view_logs.apply_locale()
        debug_preview_panel = getattr(self, "debug_preview_panel", None)
        apply_debug_locale = getattr(debug_preview_panel, "apply_locale", None)
        if callable(apply_debug_locale):
            apply_debug_locale()
        self.page.update()

    def refresh_overlay_peer_contract(self) -> None:
        controller = getattr(self, "controller", None)
        build_contract = getattr(controller, "build_overlay_peer_consumer_contract", None)
        if not callable(build_contract):
            return
        contract = build_contract()
        self.overlay_peer_contract = contract
        if contract is None:
            return
        view_settings = getattr(self, "view_settings", None)
        set_settings_contract = getattr(view_settings, "set_overlay_peer_contract", None)
        if callable(set_settings_contract):
            set_settings_contract(contract)
        view_dashboard = getattr(self, "view_dashboard", None)
        set_dashboard_contract = getattr(view_dashboard, "set_overlay_peer_contract", None)
        if callable(set_dashboard_contract):
            set_dashboard_contract(contract)

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.controller.submit_text(text)

        self.page.run_task(_task)

    def _log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        controller = getattr(self, "controller", None)
        log_basic = getattr(controller, "log_basic", None)
        if callable(log_basic):
            log_basic(message, level=level)
            return
        logger.log(level, message)

    def _log_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        controller = getattr(self, "controller", None)
        log_detailed = getattr(controller, "log_detailed", None)
        if callable(log_detailed):
            log_detailed(message, level=level)
            return
        logger.log(level, message)

    def _on_translation_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Translation toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Translation toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_translation_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        async def _task():
            await self.controller.set_translation_enabled(enabled)

        self.page.run_task(_task)

    def _on_stt_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] STT toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] STT toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_stt_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        async def _task():
            await self.controller.set_stt_enabled(enabled)

        self.page.run_task(_task)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Overlay toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Overlay toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        async def _task():
            await self.controller.set_overlay_enabled(enabled)

        self.page.run_task(_task)

    def _on_peer_translation_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Peer toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Peer toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        controller = getattr(self, "controller", None)
        settings = getattr(controller, "settings", None)
        ui_settings = getattr(settings, "ui", None)
        if (
            enabled
            and ui_settings is not None
            and not getattr(ui_settings, "peer_translation_eula_accepted", False)
        ):
            self._show_peer_translation_eula(self._accept_peer_translation_eula_and_enable)
            return

        async def _task():
            await self.controller.set_peer_translation_enabled(enabled)

        self.page.run_task(_task)

    def _on_language_change(
        self,
        source_code: str,
        target_code: str,
        peer_source_code: str = "",
        peer_target_code: str = "",
    ) -> None:
        if self.controller.settings is None:
            return
        settings = self.controller.settings
        previous_source_code = settings.languages.source_language
        previous_target_code = settings.languages.target_language
        previous_peer_source_code = getattr(settings.languages, "peer_source_language", "")
        previous_peer_target_code = getattr(settings.languages, "peer_target_language", "")
        self._log_basic(
            "[Dashboard] Language change requested: "
            f"source={previous_source_code}->{source_code} "
            f"target={previous_target_code}->{target_code} "
            f"peer_source={previous_peer_source_code}->{peer_source_code} "
            f"peer_target={previous_peer_target_code}->{peer_target_code}"
        )
        self._log_detailed(
            f"[Dashboard] Language change detail: overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        # Check STT provider compatibility and show warning if needed
        warning = None
        if source_code != previous_source_code:
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
            await self.controller.on_dashboard_language_change(
                source_code=source_code,
                target_code=target_code,
                peer_source_code=peer_source_code,
                peer_target_code=peer_target_code,
            )

        self._queue_settings_mutation_task(_task)

    def _on_settings_changed(self, settings) -> None:
        async def _task():
            await self.controller.apply_settings(settings)

        self._queue_settings_mutation_task(_task)

    def _on_prompt_apply_settings(self, settings) -> None:
        async def _task():
            merged_settings = self.controller.merge_settings_tab_apply_with_current_languages(
                settings
            )
            await self.controller.apply_settings(merged_settings)

        self._queue_settings_mutation_task(_task)

    def _on_runtime_logging_mode_change(self, mode: str) -> None:
        self.controller.set_runtime_logging_mode(mode)
        self.view_logs.set_runtime_logging_mode(self.controller.runtime_logging_mode)

    def _on_providers_changed(self) -> None:
        async def _task():
            await self.controller.apply_providers()

        self._queue_settings_mutation_task(_task)

    def _on_request_openrouter_pkce(
        self,
        target_settings: AppSettings,
        *,
        launch_source: str = "settings",
    ) -> None:
        if getattr(self, "_openrouter_pkce_request_active", False):
            reopen_authorization_url = getattr(
                self.controller,
                "reopen_openrouter_pkce_authorization_url",
                None,
            )
            if callable(reopen_authorization_url):
                reopen_authorization_url()
            return
        self._openrouter_pkce_request_active = True

        async def _task() -> None:
            try:
                ok = await self.controller.connect_openrouter_via_pkce(
                    target_settings=target_settings,
                    launch_source=launch_source,
                )
                if ok:
                    refresh_after_openrouter_pkce_success = getattr(
                        self.view_settings,
                        "refresh_after_openrouter_pkce_success",
                        None,
                    )
                    if callable(refresh_after_openrouter_pkce_success):
                        refresh_after_openrouter_pkce_success(
                            self.controller.settings,
                            config_path=self.controller.config_path,
                        )
                    else:
                        self.view_settings.load_from_settings(
                            self.controller.settings,
                            config_path=self.controller.config_path,
                            preserve_custom_vocab_draft=True,
                        )
                    self._show_snackbar(t("openrouter.pkce.connected"), COLOR_SUCCESS)
            finally:
                self._openrouter_pkce_request_active = False

        self._queue_settings_mutation_task(_task)

    def _build_founder_letter_target_settings(self) -> AppSettings | None:
        current_settings = getattr(getattr(self, "controller", None), "settings", None)
        if current_settings is None:
            return None
        if current_settings.provider.llm != LLMProviderName.OPENROUTER:
            return None
        if current_settings.openrouter.selected_source != OpenRouterCredentialSource.MANAGED:
            return None

        openrouter_model = None
        selection_alias = current_settings.openrouter.selection_alias
        if selection_alias is not None:
            try:
                profile = profile_for_alias(selection_alias.value)
            except KeyError:
                profile = None
            if profile is not None:
                openrouter_model = profile.openrouter_model
        if openrouter_model is None:
            openrouter_model = current_settings.openrouter.llm_model.value

        alias_value = get_openrouter_selection_alias_for_model_and_source(
            openrouter_model,
            OpenRouterCredentialSource.BYOK.value,
        )
        if alias_value is None:
            return None

        target_settings = copy.deepcopy(current_settings)
        target_settings.provider.llm = LLMProviderName.OPENROUTER
        target_settings.openrouter.selection_alias = OpenRouterSelectionAlias(alias_value)
        target_settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
        target_settings.openrouter.llm_model = OpenRouterLLMModel(openrouter_model)
        return target_settings

    def _on_founder_letter_connect(self) -> None:
        target_settings = self._build_founder_letter_target_settings()
        if target_settings is None:
            self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)
            return
        self._on_request_openrouter_pkce(target_settings, launch_source="letter")

    def _on_founder_letter_contact(self) -> None:
        webbrowser.open(FOUNDER_CONTACT_URL)

    def show_founder_letter_dialog(self) -> None:
        dialog = FounderLetterDialog(
            self.page,
            on_connect=self._on_founder_letter_connect,
            on_contact=self._on_founder_letter_contact,
        )
        self._founder_letter_dialog = dialog
        dialog.open()

    async def _on_verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        success, msg = await self.controller.verify_api_key(provider, key)

        # Save verification result to settings
        setattr(self.controller.settings.api_key_verified, provider, success)
        save_settings(self.controller.config_path, self.controller.settings)

        # Sync verification result with dashboard needs_key flags (UI update on user click)
        if provider in ("deepgram", "soniox", "qwen_asr"):
            self.view_dashboard.set_stt_needs_key(not success, update_ui=False)
        elif provider in ("google", "openrouter", "alibaba_beijing", "alibaba_singapore"):
            self.view_dashboard.set_translation_needs_key(not success, update_ui=False)

        return success, msg

    def _on_secret_cleared(self, key: str) -> None:
        """Reset verification status when API key is cleared."""
        # Map secret key name to provider name
        key_to_provider = {
            "deepgram_api_key": "deepgram",
            "soniox_api_key": "soniox",
            "google_api_key": "google",
            "openrouter_api_key": "openrouter",
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
            elif provider in ("google", "openrouter", "alibaba_beijing", "alibaba_singapore"):
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

    def on_overlay_state_changed(
        self,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        previous_state = getattr(self, "overlay_state", "unknown")
        self._log_basic(f"[Overlay] State changed: {previous_state} -> {state}")
        self.overlay_state = state
        self.overlay_failure_reason = failure_reason
        self._log_detailed(
            f"[Overlay] State detail: overlay_state={state} failure_reason={failure_reason}"
        )
        self.view_settings.set_overlay_runtime_state(state, failure_reason=failure_reason)
        self.refresh_overlay_peer_contract()


async def main_gui(page: ft.Page, *, config_path, debug_ui_preview: bool = False):
    app = TranslatorApp(
        page,
        config_path=config_path,
        debug_ui_preview=debug_ui_preview,
    )
    await app.controller.start()

    # Check for updates in background
    await _check_and_notify_update(page, log_detailed=app._log_detailed)


async def _check_and_notify_update(page: ft.Page, log_detailed=None) -> None:
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
        message = f"[Update] Check notification failed: {exc}"
        if callable(log_detailed):
            log_detailed(message)
            return
        logger.debug(message)
