import asyncio
import contextlib
import inspect
import logging
import tempfile
import webbrowser
from pathlib import Path

import flet as ft

from puripuly_heart.app.language_selection import LanguageSelectionChange
from puripuly_heart.app.ports.dashboard_application import (
    ChangeDashboardLanguages,
    DashboardApplicationPort,
    SetManualInputActivity,
    SetOverlayEnabled,
    SetPeerEulaAccepted,
    SetPeerTranslationEnabled,
    SetRuntimeLoggingMode,
    SetSelfSttEnabled,
    SetTelemetryConsent,
    SetTranslationEnabled,
    SubmitManualSelfText,
)
from puripuly_heart.app.ports.managed_authentication_application import (
    EphemeralSecretLease,
    ManagedAuthenticationStatus,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)
from puripuly_heart.app.ports.overlay_application import (
    AudioCaptureGatePort,
    OverlayApplicationCommandPort,
    OverlayApplicationStatePort,
)
from puripuly_heart.app.ports.post_commit_runtime import SurfaceRuntimeTransactionPort
from puripuly_heart.app.services.ui_settings import UiSettingsApplication
from puripuly_heart.core.discord_oauth_loopback import (
    render_discord_oauth_callback_completion_page,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.core.managed_openrouter_release import TalkTogetherPassStatus
from puripuly_heart.core.updater import check_for_update
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.components.debug_preview_panel import DebugPreviewPanel
from puripuly_heart.ui.components.discord_managed_auth_dialog import DiscordManagedAuthDialog
from puripuly_heart.ui.components.founder_letter_dialog import FounderLetterDialog
from puripuly_heart.ui.components.local_qwen_hallucination_dialog import (
    LocalQwenHallucinationDialog,
)
from puripuly_heart.ui.components.microphone_test_dialog import MicrophoneTestDialog
from puripuly_heart.ui.components.peer_translation_eula_dialog import PeerTranslationEulaDialog
from puripuly_heart.ui.components.qq_managed_auth_dialog import QqManagedAuthDialog
from puripuly_heart.ui.components.telemetry_consent_dialog import TelemetryConsentDialog
from puripuly_heart.ui.components.title_bar import TitleBar
from puripuly_heart.ui.event_bridge import ApplicationUIEventBridgeFactory
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
from puripuly_heart.ui.views.application_settings import ApplicationSettingsView
from puripuly_heart.ui.views.dashboard import DashboardView
from puripuly_heart.ui.views.logs import LogsView

logger = logging.getLogger(__name__)
MANAGED_AUTH_DYNAMIC_I18N_KEYS = (
    "peer_translation.disclosure",
    "qq_auth.error.already_claimed_discord",
    "qq_auth.error.broker_unavailable",
    "qq_auth.error.lifetime_used",
    "qq_auth.error.rate_limited",
    "qq_auth.error.secret_write_failed",
    "qq_auth.error.settings_commit_failed",
    "qq_managed_auth.error.retry",
)

DEFAULT_WINDOW_WIDTH = 1136
DEFAULT_WINDOW_HEIGHT = 850
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 760
APP_CONTENT_PADDING = 16
FOUNDER_CONTACT_URL = "https://x.com/kapitalismho"
FOUNDER_README_BASE_URL = "https://github.com/kapitalismho/PuriPuly-heart/blob/main"
FOUNDER_README_PATH_BY_LOCALE = {
    "ko": "README.ko.md",
    "zh-CN": "README.zh-CN.md",
    "ja": "README.ja.md",
}
FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE = {
    "ko": "자신의-api-키-사용하기",
    "zh-CN": "使用您自己的-api-密钥",
    "ja": "自分のapiキーを使う",
}
FOUNDER_README_DEFAULT_API_KEYS_ANCHOR = "using-your-own-api-keys"
DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID = "7KQ9M2"
GITHUB_STAR_REPOSITORY_URL = "https://github.com/kapitalismho/PuriPuly-heart"
GITHUB_STAR_PROMPT_DELAY_S = 2.5
GITHUB_STAR_PROMPT_DURATION_MS = 8000


def _callable_accepts_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def founder_readme_url_for_locale(locale: str | None) -> str:
    readme_path = FOUNDER_README_PATH_BY_LOCALE.get(locale or "", "README.md")
    anchor = FOUNDER_README_API_KEYS_ANCHOR_BY_LOCALE.get(
        locale or "", FOUNDER_README_DEFAULT_API_KEYS_ANCHOR
    )
    return f"{FOUNDER_README_BASE_URL}/{readme_path}#{anchor}"


def _write_discord_callback_preview_page(locale: str | None) -> str:
    html = render_discord_oauth_callback_completion_page(locale)
    with tempfile.NamedTemporaryFile(
        "wb",
        prefix="puripuly-discord-callback-",
        suffix=".html",
        delete=False,
    ) as handle:
        handle.write(html)
        path = Path(handle.name)
    return path.as_uri()


class TranslatorApp:
    def __init__(
        self,
        page: ft.Page,
        *,
        config_path,
        debug_ui_preview: bool = False,
        allow_stable_settings_import: bool = False,
        surface_runtime_transactions: SurfaceRuntimeTransactionPort | None = None,
        overlay_commands: OverlayApplicationCommandPort | None = None,
        overlay_application_state: OverlayApplicationStatePort | None = None,
        overlay_ui_projection: object | None = None,
        vrc_audio_gate: AudioCaptureGatePort | None = None,
        application_runtime_host: object | None = None,
        application_adapters: object | None = None,
        ui_settings: UiSettingsApplication,
        dashboard: DashboardApplicationPort,
    ):
        self.page = page
        self.dashboard = dashboard
        self.overlay_commands = overlay_commands
        self.overlay_state = "off"
        self.overlay_failure_reason: str | None = None
        self.debug_ui_preview = bool(debug_ui_preview)
        self.debug_preview_panel: DebugPreviewPanel | None = None
        self._application_adapters = application_adapters
        self.ui_settings = ui_settings
        self._github_star_prompt_launch_pending = True
        self._github_star_prompt_presentation = None
        self._managed_authentication_presentation = None
        self._launch_high_priority_feedback_shown = False
        self._launch_high_priority_feedback_reason: str | None = None
        self._launch_high_priority_snackbar = None
        self._github_star_prompt_shown_this_launch = False
        self._microphone_test_dialog: MicrophoneTestDialog | None = None
        self._telemetry_consent_dialog: TelemetryConsentDialog | None = None
        self._dashboard_snapshot = None
        self._setup_page()
        self._build_layout()
        if overlay_ui_projection is not None:
            subscribe_dashboard = getattr(overlay_ui_projection, "subscribe_dashboard", None)
            if callable(subscribe_dashboard):
                subscribe_dashboard(self._apply_dashboard_runtime_facts)
            subscribe_desktop = getattr(overlay_ui_projection, "subscribe_desktop", None)
            if callable(subscribe_desktop):
                subscribe_desktop(self._apply_desktop_renderer_projection)

        # Link Dashboard callbacks
        self.view_dashboard.on_send_message = self._on_manual_submit
        self.view_dashboard.on_message_input_activity = self._on_message_input_activity
        self.view_dashboard.on_toggle_translation = self._on_translation_toggle
        self.view_dashboard.on_toggle_stt = self._on_stt_toggle
        self.view_dashboard.on_toggle_overlay = self._on_overlay_toggle
        self.view_dashboard.on_toggle_peer_translation = self._on_peer_translation_toggle
        self.view_dashboard.on_retry_peer_process_capture = self._on_retry_peer_process_capture
        self.view_dashboard.on_language_change = self._on_language_change

        self.view_settings.on_snackbar = self._show_settings_snackbar
        self.view_settings.on_locale_changed = self._on_settings_locale_changed
        self.view_logs.on_mode_change = self._on_runtime_logging_mode_change
        self.view_logs.set_runtime_logging_mode(self.dashboard.runtime_logging_mode)
        self.view_dashboard.runtime_log_detailed = self._log_detailed

    def create_presentation_lifecycle(self):  # noqa: ANN201
        return self.dashboard.create_presentation_lifecycle(
            view=self,
            bridge_factory=ApplicationUIEventBridgeFactory(self),
        )

    async def prepare_dashboard(self) -> None:
        snapshot = await self.dashboard.snapshot()
        self._dashboard_snapshot = snapshot
        self._github_star_prompt_presentation = (
            await self.dashboard.github_star_prompt_presentation()
        )
        self._managed_authentication_presentation = (
            await self.dashboard.managed_authentication_presentation()
        )
        self.view_dashboard.set_translation_enabled(snapshot.translation_enabled)
        self.view_dashboard.set_stt_enabled(snapshot.self_stt_enabled)
        self.view_dashboard.set_overlay_peer_presentation(snapshot.overlay_peer)
        self.view_logs.set_runtime_logging_mode(snapshot.runtime_logging_mode)

    async def start_dashboard(self) -> None:
        return None

    def on_managed_authentication_presentation(self, presentation) -> None:  # noqa: ANN001
        self._managed_authentication_presentation = presentation
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        if dialog is None:
            return
        dialog.set_reopen_available(
            presentation.browser_reopen_available,
            self._reopen_discord_managed_auth_browser,
        )
        if presentation.callback_received:
            dialog.set_callback_received()

    async def freeze_dashboard_ingress(self) -> None:
        await self.dashboard.cancel_managed_authentication()

    async def stop_dashboard(self, failures: tuple[BaseException, ...]) -> None:
        _ = failures

    def _show_settings_snackbar(self, message: str, severity: str) -> None:
        color = {
            "success": COLOR_SUCCESS,
            "warning": ft.Colors.ORANGE_700,
            "error": ft.Colors.RED_700,
        }.get(severity)
        self._show_snackbar(message, color)

    def add_history_entry(
        self,
        source: str,
        text: str,
        *,
        translated: bool = False,
        language_code: str | None = None,
    ) -> None:
        self.view_dashboard.history_items.append((source, text, translated, language_code))

    def _on_settings_locale_changed(self, _locale: str) -> None:
        self.apply_locale()

    def _apply_dashboard_runtime_facts(self, facts: object) -> None:
        self.view_dashboard.translation_needs_key = not bool(getattr(facts, "llm_available", False))
        self.view_dashboard.stt_needs_key = not bool(getattr(facts, "self_stt_available", False))

    def _apply_desktop_renderer_projection(self, projection: object) -> None:
        event = getattr(projection, "event", None)
        bounds = getattr(projection, "bounds", None)
        source = getattr(projection, "source", None)
        persist = getattr(projection, "persist", False)
        if (
            event == "window_bounds_changed"
            and source == "user"
            and persist is True
            and isinstance(bounds, tuple)
            and len(bounds) == 4
            and self.overlay_commands is not None
        ):
            payload = dict(zip(("x", "y", "width", "height"), bounds, strict=True))

            async def _persist_bounds() -> None:
                await self.overlay_commands.persist_desktop_bounds(payload)

            self.page.run_task(_persist_bounds)
        elif (
            event == "reset_to_bottom_center_requested"
            or (event == "window_bounds_changed" and source == "reset")
        ) and self.overlay_commands is not None:

            async def _reset_position() -> None:
                await self.overlay_commands.reset_desktop_position()

            self.page.run_task(_reset_position)
        mode = getattr(projection, "interaction_mode", None)
        if mode is not None:
            if self.overlay_commands is not None:
                self.overlay_commands.apply_desktop_interaction_mode_event(mode)
            self.on_desktop_overlay_state_changed(
                interaction_mode=mode,
                captions_locked=mode in {"locked", "pass_through"},
            )

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
        self.page.on_keyboard_event = self._on_keyboard_event

    def _build_layout(self):
        self.view_dashboard = DashboardView()
        self.view_settings = ApplicationSettingsView(self.ui_settings)
        self.view_logs = LogsView()
        self.view_about = AboutView()

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
            on_discord_auth=self._preview_discord_auth,
            on_qq_auth=self._preview_qq_auth,
            on_qq_auth_recoverable_error=self._preview_qq_auth_recoverable_error,
            on_qq_auth_translation_gated=self._preview_qq_auth_translation_gated,
            on_discord_callback_page=self._preview_discord_callback_page,
            on_peer_translation_eula=self._preview_peer_translation_eula,
            on_local_qwen_hallucination_modal=self._preview_local_qwen_hallucination_modal,
            on_talk_together_pass_invite_progress=(
                self._preview_talk_together_pass_invite_progress
            ),
            on_capture_fault_cycle=self._preview_capture_fault_cycle,
            on_stt_fault_cycle=self._preview_stt_fault_cycle,
            on_audio_fault_clear=self._preview_audio_fault_clear,
            on_github_star_snackbar=self._preview_github_star_snackbar,
            on_telemetry_consent=self._preview_telemetry_consent,
        )

    def _mark_launch_high_priority_feedback_shown(
        self,
        reason: str,
        snackbar: object | None = None,
    ) -> None:
        if not getattr(self, "_github_star_prompt_launch_pending", True):
            return
        self._launch_high_priority_feedback_shown = True
        self._launch_high_priority_feedback_reason = reason
        if snackbar is not None:
            self._launch_high_priority_snackbar = snackbar

    def _launch_feedback_conflicts_with_github_star_prompt(self) -> bool:
        if getattr(self, "_launch_high_priority_feedback_shown", False):
            return True
        snackbar = getattr(self, "_launch_high_priority_snackbar", None)
        return bool(getattr(snackbar, "open", False))

    async def maybe_show_github_star_prompt_after_launch(
        self,
        *,
        delay_s: float = GITHUB_STAR_PROMPT_DELAY_S,
    ) -> bool:
        try:
            result = await self.dashboard.run_delayed_github_star_launch(delay_s)
            self._github_star_prompt_presentation = result.presentation
            if self._launch_feedback_conflicts_with_github_star_prompt():
                return False
            if result.status.value != "applied" or not result.presentation.should_show:
                return False
            return await self._open_github_star_prompt_snackbar(
                should_open=lambda: not self._launch_feedback_conflicts_with_github_star_prompt()
            )
        finally:
            self._github_star_prompt_launch_pending = False

    async def close_github_star_prompt_runtime(self) -> None:
        await self.dashboard.cancel_github_star_launch()
        self._github_star_prompt_launch_pending = False

    async def _open_github_star_prompt_snackbar(self, *, should_open=None) -> bool:  # noqa: ANN001
        if getattr(self, "_github_star_prompt_shown_this_launch", False):
            return False
        if should_open is not None and not should_open():
            return False
        result = await self.dashboard.record_github_star_opened()
        self._github_star_prompt_presentation = result.presentation
        if result.status.value != "applied":
            return False

        snackbar = None

        def _open_repository(_event) -> None:  # noqa: ANN001
            async def _persist_click() -> None:
                result = await self.dashboard.record_github_star_clicked()
                self._github_star_prompt_presentation = result.presentation

            self._queue_settings_mutation_task(_persist_click)
            webbrowser.open(GITHUB_STAR_REPOSITORY_URL)
            if snackbar is not None:
                self._close_github_star_prompt_snackbar(snackbar)

        snackbar = self._build_github_star_prompt_snackbar(_open_repository)
        self._github_star_prompt_shown_this_launch = True
        self.page.open(snackbar)
        return True

    def _build_github_star_prompt_snackbar(self, on_click) -> ft.SnackBar:  # noqa: ANN001
        return ft.SnackBar(
            content=ft.Row(
                controls=[
                    ft.Text(
                        t("github_star.snackbar.message"),
                        size=18,
                        color=ft.Colors.WHITE,
                        font_family=font_for_language(get_locale()),
                        expand=True,
                    ),
                    ft.TextButton(
                        text=t("github_star.snackbar.action"),
                        on_click=on_click,
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
            duration=GITHUB_STAR_PROMPT_DURATION_MS,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
        )

    def _close_github_star_prompt_snackbar(self, snackbar: ft.SnackBar) -> None:
        close = getattr(self.page, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close(snackbar)
        else:
            snackbar.open = False
            with contextlib.suppress(Exception):
                self.page.update()
        self._displace_current_snackbar_for_flet_028()

    def _displace_current_snackbar_for_flet_028(self) -> None:
        """Force-dismiss the visible SnackBar on Flet 0.28.x.

        Flet 0.28.3 updates the Python-side ``SnackBar.open`` flag on
        ``page.close(snackbar)`` but the Flutter-side snackbar remains visible
        until its duration expires. Opening another SnackBar first removes the
        current one, so use a transparent 1 ms replacement as a narrow shim.
        """

        open_control = getattr(self.page, "open", None)
        if not callable(open_control):
            return
        dismissor = ft.SnackBar(
            content=ft.Text("", size=0),
            bgcolor=ft.Colors.TRANSPARENT,
            duration=1,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=0,
        )
        with contextlib.suppress(Exception):
            open_control(dismissor)

    def _preview_github_star_snackbar(self) -> None:
        snackbar = None

        def _open_repository(_event) -> None:  # noqa: ANN001
            webbrowser.open(GITHUB_STAR_REPOSITORY_URL)
            if snackbar is not None:
                self._close_github_star_prompt_snackbar(snackbar)

        snackbar = self._build_github_star_prompt_snackbar(_open_repository)
        self.page.open(snackbar)

    def _preview_telemetry_consent(self) -> None:
        dialog = TelemetryConsentDialog(
            self.page,
            on_allow=self._debug_preview_noop,
            on_decline=self._debug_preview_noop,
        )
        self._telemetry_consent_dialog = dialog
        dialog.open()

    def _preview_brake_notice(self) -> None:
        self._show_snackbar(t("managed_release.brake"), ft.Colors.ORANGE_700)

    def _preview_revoked_notice(self) -> None:
        self._show_snackbar(t("managed_release.revoked_contact"), ft.Colors.ORANGE_700)

    def _debug_preview_noop(self) -> None:
        return None

    def _preview_founder_letter(self) -> None:
        dialog = FounderLetterDialog(self.page, on_readme=self._on_founder_letter_readme)
        self._founder_letter_dialog = dialog
        dialog.open()

    def _preview_pkce_failure(self) -> None:
        self._show_snackbar(t("openrouter.pkce.failed"), ft.Colors.ORANGE_700)

    def _preview_discord_auth(self) -> None:
        self.show_discord_managed_auth_dialog(preview=True)

    def _open_qq_auth_preview_dialog(self) -> QqManagedAuthDialog:
        dialog = QqManagedAuthDialog(
            self.page,
            on_continue=self._close_qq_managed_auth_dialog,
            on_close=self._close_qq_managed_auth_dialog,
            on_cancel=self._close_qq_managed_auth_dialog,
        )
        self._qq_managed_auth_dialog = dialog
        dialog.open()
        return dialog

    def _preview_qq_auth(self) -> None:
        self._open_qq_auth_preview_dialog()

    def _preview_qq_auth_recoverable_error(self) -> None:
        dialog = self._open_qq_auth_preview_dialog()
        dialog.set_error("qq_auth.error.credential_mismatch")

    def _preview_qq_auth_translation_gated(self) -> None:
        dialog = self._open_qq_auth_preview_dialog()
        dialog.set_error("qq_auth.error.key_unavailable")

    def _preview_discord_callback_page(self) -> None:
        webbrowser.open(_write_discord_callback_preview_page(get_locale()))

    def _preview_peer_translation_eula(self) -> None:
        self._show_peer_translation_eula(self._debug_preview_noop)

    def _preview_local_qwen_hallucination_modal(self) -> None:
        self.show_local_qwen_hallucination_dialog()

    def _preview_talk_together_pass_invite_progress(self) -> None:
        set_managed_key_state = getattr(self.view_settings, "set_managed_key_state", None)
        if not callable(set_managed_key_state):
            return
        set_managed_key_state(
            visible=True,
            remaining_percent=100,
            referral_id=DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID,
            remember_referral_id=False,
            pass_status=TalkTogetherPassStatus(
                pass_id=DEBUG_PREVIEW_TALK_TOGETHER_PASS_ID,
                invite_count=1,
                invite_limit=5,
                bonus_translations_per_friend=200,
            ),
        )

    def _preview_capture_fault_cycle(self) -> None:
        profile = self.dashboard.cycle_debug_capture_fault_profile()
        self._show_snackbar(
            t("debug_preview.capture_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_stt_fault_cycle(self) -> None:
        profile = self.dashboard.cycle_debug_stt_fault_profile()
        self._show_snackbar(
            t("debug_preview.stt_fault_snackbar", profile=profile), ft.Colors.ORANGE_700
        )

    def _preview_audio_fault_clear(self) -> None:
        self.dashboard.clear_debug_audio_fault_profiles()
        self._show_snackbar(t("debug_preview.audio_fault_clear"), ft.Colors.GREEN_700)

    def _show_peer_translation_eula(self, on_accept) -> None:
        dialog = PeerTranslationEulaDialog(
            self.page,
            on_accept=on_accept,
            on_cancel=self._debug_preview_noop,
        )
        self._peer_translation_eula_dialog = dialog
        dialog.open()

    def maybe_show_telemetry_consent_dialog(self) -> bool:
        snapshot = self._dashboard_snapshot
        if snapshot is None or snapshot.telemetry_consent not in {"unknown", "unset"}:
            return False
        dialog = TelemetryConsentDialog(
            self.page,
            on_allow=lambda: self._on_telemetry_consent_change("allow"),
            on_decline=lambda: self._on_telemetry_consent_change("decline"),
        )
        self._telemetry_consent_dialog = dialog
        dialog.open()
        self._mark_launch_high_priority_feedback_shown("telemetry_consent")
        return True

    def _on_telemetry_consent_change(self, consent: str) -> None:
        if consent not in {"allow", "decline"}:
            return

        async def _task() -> None:
            result = await self.dashboard.set_telemetry_consent(SetTelemetryConsent(consent))
            if result.status.value != "applied":
                return
            self._dashboard_snapshot = await self.dashboard.snapshot()

        self.page.run_task(_task)

    def show_local_qwen_hallucination_dialog(self) -> None:
        dialog = LocalQwenHallucinationDialog(
            self.page,
            on_open_guide=self._open_local_qwen_guide,
        )
        self._local_qwen_hallucination_dialog = dialog
        dialog.open()

    def _open_local_qwen_guide(self) -> None:
        webbrowser.open(founder_readme_url_for_locale(get_locale()))

    def _accept_peer_translation_eula_and_enable(self) -> None:
        async def _task():
            snapshot = self._dashboard_snapshot or await self.dashboard.snapshot()
            result = await self.dashboard.set_peer_eula(
                SetPeerEulaAccepted(True, snapshot.settings.operational_revision)
            )
            if result.status.value != "applied":
                return
            self._dashboard_snapshot = await self.dashboard.snapshot()
            peer_result = await self.dashboard.set_peer_translation_enabled(
                SetPeerTranslationEnabled(True)
            )
            self._dashboard_snapshot = peer_result.snapshot
            self.view_dashboard.set_overlay_peer_presentation(peer_result.snapshot.overlay_peer)

        self.page.run_task(_task)

    def _close_open_dialog_for_navigation(self) -> None:
        microphone_test_dialog = getattr(self, "_microphone_test_dialog", None)
        if microphone_test_dialog is not None and getattr(
            microphone_test_dialog,
            "is_open",
            False,
        ):
            microphone_test_dialog.close(notify=True)
            return

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
        previous_tab = getattr(self, "_current_tab", 0)
        if previous_tab != index:
            self._close_open_dialog_for_navigation()
        self._current_tab = index

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
        if index == 2:
            # Async scroll after rendering completes
            async def _scroll():
                import asyncio

                await asyncio.sleep(0.05)
                await self.view_logs.scroll_to_bottom()

            self.page.run_task(_scroll)

    def _open_logs_tab(self) -> None:
        self._on_nav_change(2)
        self._set_bottom_nav_selected(2)

    def _open_settings_tab(self) -> None:
        self._on_nav_change(1)
        self._set_bottom_nav_selected(1)

    def _set_bottom_nav_selected(self, index: int) -> None:
        selected_attr = getattr(self.bottom_nav, "_selected", None)
        if selected_attr != index and hasattr(self.bottom_nav, "_selected"):
            self.bottom_nav._selected = index
        update_visuals = getattr(self.bottom_nav, "_update_visuals", None)
        if callable(update_visuals):
            with contextlib.suppress(Exception):
                update_visuals()

    def apply_locale(self) -> None:
        self.page.title = t("app.title")
        self.page.theme = get_app_theme(font_family=font_for_language(get_locale()))
        self.title_bar.set_title(t("app.title"))
        self.view_dashboard.apply_locale()
        self.view_logs.apply_locale()
        debug_preview_panel = getattr(self, "debug_preview_panel", None)
        apply_debug_locale = getattr(debug_preview_panel, "apply_locale", None)
        if callable(apply_debug_locale):
            apply_debug_locale()
        self.page.update()

    def _on_desktop_overlay_recovery_action(self, action: str) -> None:
        if action not in {"retry", "reopen"}:
            return

        async def _task():
            result = await self.dashboard.set_overlay_enabled(SetOverlayEnabled(True))
            self._dashboard_snapshot = result.snapshot

        self.page.run_task(_task)

    def on_desktop_overlay_state_changed(
        self,
        *,
        interaction_mode: str | None = None,
        captions_locked: bool | None = None,
    ) -> None:
        _ = (interaction_mode, captions_locked)

    def _on_manual_submit(self, _source: str, text: str) -> None:
        async def _task():
            await self.dashboard.submit_manual_self_text(SubmitManualSelfText(text))

        self.page.run_task(_task)

    def _on_message_input_activity(self, has_text: bool) -> None:
        async def _task():
            self.dashboard.set_manual_input_activity(SetManualInputActivity(has_text))

        self.page.run_task(_task)

    def _on_keyboard_event(self, event) -> None:
        if getattr(event, "key", None) != "Tab":
            return
        if any(
            bool(getattr(event, modifier, False)) for modifier in ("shift", "ctrl", "alt", "meta")
        ):
            return

        dashboard = getattr(self, "view_dashboard", None)
        content_area = getattr(self, "content_area", None)
        if dashboard is None or getattr(content_area, "content", None) is not dashboard:
            return

        handler = getattr(dashboard, "handle_message_input_tab_key", None)
        if callable(handler):
            handler()

    def _log_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self.dashboard.log_basic(message, level=level)

    def _log_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        self.dashboard.log_detailed(message, level=level)

    def _revert_dashboard_translation_toggle(self) -> None:
        self._set_dashboard_translation_visual_state(False)

    def _set_dashboard_translation_visual_state(self, enabled: bool) -> None:
        dash = getattr(self, "view_dashboard", None)
        set_translation_enabled = getattr(dash, "set_translation_enabled", None)
        if callable(set_translation_enabled):
            try:
                set_translation_enabled(enabled)
            except Exception:
                logger.exception("Failed to update dashboard translation toggle")

    def _dashboard_managed_auth_action(self) -> str:
        presentation = self._managed_authentication_presentation
        return "prompt" if presentation is None else presentation.action

    def _dashboard_managed_auth_prompt_kind(self) -> str:
        presentation = self._managed_authentication_presentation
        return "discord" if presentation is None else presentation.prompt.value

    def _on_translation_toggle(self, enabled: bool) -> bool:
        self._log_basic(f"[Dashboard] Translation toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Translation toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_translation_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )
        if enabled:
            managed_auth_action = self._dashboard_managed_auth_action()
            if managed_auth_action in {"prompt", "in_progress"}:
                self._revert_dashboard_translation_toggle()
                if managed_auth_action == "prompt":
                    if self._dashboard_managed_auth_prompt_kind() == "qq":
                        self.show_qq_managed_auth_dialog()
                    else:
                        self.show_discord_managed_auth_dialog(preview=False)
                return False

        async def _task():
            await self.dashboard.set_translation_enabled(SetTranslationEnabled(enabled))
            self._dashboard_snapshot = await self.dashboard.snapshot()
            self.view_dashboard.set_translation_enabled(
                self._dashboard_snapshot.translation_enabled
            )

        self.page.run_task(_task)
        return True

    def _on_stt_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] STT toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] STT toggle detail: "
            f"dashboard_state={getattr(getattr(self, 'view_dashboard', None), 'is_stt_on', None)} "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        async def _task():
            await self.dashboard.set_self_stt_enabled(SetSelfSttEnabled(enabled))
            self._dashboard_snapshot = await self.dashboard.snapshot()
            self.view_dashboard.set_stt_enabled(self._dashboard_snapshot.self_stt_enabled)

        self.page.run_task(_task)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Overlay toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Overlay toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        async def _task():
            result = await self.dashboard.set_overlay_enabled(SetOverlayEnabled(enabled))
            self._dashboard_snapshot = result.snapshot
            self.view_dashboard.set_overlay_peer_presentation(result.snapshot.overlay_peer)

        self.page.run_task(_task)

    def _on_peer_translation_toggle(self, enabled: bool) -> None:
        self._log_basic(f"[Dashboard] Peer toggle requested: enabled={enabled}")
        self._log_detailed(
            "[Dashboard] Peer toggle detail: "
            f"overlay_state={getattr(self, 'overlay_state', 'unknown')} "
            f"failure_reason={getattr(self, 'overlay_failure_reason', None)}"
        )

        snapshot = self._dashboard_snapshot
        if enabled and snapshot is not None and not snapshot.peer_eula_accepted:
            self._show_peer_translation_eula(self._accept_peer_translation_eula_and_enable)
            return

        async def _task():
            result = await self.dashboard.set_peer_translation_enabled(
                SetPeerTranslationEnabled(enabled)
            )
            self._dashboard_snapshot = result.snapshot
            self.view_dashboard.set_overlay_peer_presentation(result.snapshot.overlay_peer)

        self.page.run_task(_task)

    def _on_retry_peer_process_capture(self) -> None:
        self._log_basic("[Dashboard] Peer process capture retry requested")

        async def _task():
            await self.dashboard.retry_capture()

        self._queue_settings_mutation_task(_task)

    def _on_language_change(
        self,
        change: LanguageSelectionChange,
    ) -> None:
        snapshot = self._dashboard_snapshot
        if snapshot is None:
            return
        languages = snapshot.settings.languages
        previous_source_code = languages.source
        previous_target_code = languages.target
        previous_peer_source_code = languages.peer_source or ""
        previous_peer_target_code = languages.peer_target or ""
        self._log_basic(
            "[Dashboard] Language change requested: "
            f"source={previous_source_code}->{change.source_code} "
            f"target={previous_target_code}->{change.target_code} "
            f"peer_source={previous_peer_source_code}->{change.peer_source_code} "
            f"peer_target={previous_peer_target_code}->{change.peer_target_code}"
        )
        self._log_detailed(
            f"[Dashboard] Language change detail: overlay_state={getattr(self, 'overlay_state', 'unknown')}"
        )

        # Check STT provider compatibility and show warning if needed
        warning = None
        if change.source_code != previous_source_code:
            stt_provider = snapshot.settings.stt.self_provider
            warning = get_stt_compatibility_warning(change.source_code, stt_provider)
        if warning:
            snackbar = ft.SnackBar(
                ft.Text(t(warning.key, language=language_name(warning.language_code))),
                bgcolor=ft.Colors.ORANGE_700,
                duration=4000,
                behavior=ft.SnackBarBehavior.FLOATING,
                margin=ft.margin.only(bottom=90),
                padding=20,
            )
            self._mark_launch_high_priority_feedback_shown("stt_compatibility", snackbar)
            self.page.open(snackbar)

        async def _task():
            result = await self.dashboard.change_languages(
                ChangeDashboardLanguages(change, snapshot.settings.canonical_revision)
            )
            if result.status.value == "applied":
                self._dashboard_snapshot = await self.dashboard.snapshot()

        self._queue_settings_mutation_task(_task)

    def _on_runtime_logging_mode_change(self, mode: str) -> None:
        self.dashboard.set_runtime_logging_mode(SetRuntimeLoggingMode(mode))
        self.view_logs.set_runtime_logging_mode(self.dashboard.runtime_logging_mode)

    def _close_discord_managed_auth_dialog(self) -> None:
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        close = getattr(dialog, "close", None)
        if callable(close):
            close()

    def show_discord_managed_auth_dialog(self, preview: bool = False) -> None:
        if not preview:
            self._mark_launch_high_priority_feedback_shown("auth_required")
        if preview:
            on_continue = self._close_discord_managed_auth_dialog
            on_byok = self._close_discord_managed_auth_dialog
            on_close = self._close_discord_managed_auth_dialog
            on_reopen_browser = self._close_discord_managed_auth_dialog
            on_cancel = self._close_discord_managed_auth_dialog
        else:
            on_continue = self._start_discord_managed_auth
            on_byok = self._on_discord_managed_auth_byok
            on_close = self._close_discord_managed_auth_dialog
            on_reopen_browser = (
                self._reopen_discord_managed_auth_browser
                if self._supports_discord_managed_auth_reopen()
                else None
            )
            on_cancel = self._cancel_discord_managed_auth

        dialog = DiscordManagedAuthDialog(
            self.page,
            on_continue=on_continue,
            on_byok=on_byok,
            on_close=on_close,
            on_reopen_browser=on_reopen_browser,
            on_cancel=on_cancel,
        )
        self._discord_managed_auth_dialog = dialog
        dialog.open()

    def show_qq_managed_auth_dialog(self) -> None:
        self._mark_launch_high_priority_feedback_shown("auth_required")
        dialog = QqManagedAuthDialog(
            self.page,
            on_continue=self._start_qq_managed_auth,
            on_close=self._close_qq_managed_auth_dialog,
            on_cancel=self._cancel_qq_managed_auth,
        )
        self._qq_managed_auth_dialog = dialog
        dialog.open()

    def _close_qq_managed_auth_dialog(self) -> None:
        dialog = getattr(self, "_qq_managed_auth_dialog", None)
        if dialog is not None:
            close = getattr(dialog, "close", None)
            if callable(close):
                close()

    def _start_qq_managed_auth(self) -> None:
        dialog = getattr(self, "_qq_managed_auth_dialog", None)
        qq_identity = getattr(dialog, "qq_identity", "")
        credential = getattr(dialog, "credential", "")
        dialog.clear_credential()
        set_waiting = getattr(dialog, "set_waiting", None)
        if callable(set_waiting):
            set_waiting()

        async def _task() -> None:
            try:
                result = await self.dashboard.start_qq_managed_authentication(
                    StartQqManagedAuthentication(
                        qq_identity, EphemeralSecretLease.from_text(credential)
                    )
                )
            except asyncio.CancelledError:
                return
            except Exception:
                self._log_basic("[ManagedAuth] QQ auth task failed", level=logging.ERROR)
                result = None
            if result is not None:
                self._managed_authentication_presentation = result.presentation
            if result is not None and result.status == ManagedAuthenticationStatus.APPLIED:
                enable_result = await self.dashboard.set_translation_enabled(
                    SetTranslationEnabled(True)
                )
                if not self._translation_enable_succeeded(self.dashboard, enable_result):
                    set_error = getattr(dialog, "set_error", None)
                    if callable(set_error):
                        set_error("qq_auth.error.retry")
                    return
                self._close_qq_managed_auth_dialog()
                self._show_snackbar(t("qq_auth.success"), COLOR_SUCCESS)
                self._set_dashboard_translation_visual_state(True)
                return
            message_key = "qq_auth.error.retry"
            message_kwargs: dict[str, object] = {}
            if result is not None and result.detail_code:
                message_key = result.detail_code
            set_error = getattr(dialog, "set_error", None)
            if callable(set_error):
                set_error(message_key, **message_kwargs)

        self.page.run_task(_task)

    def _cancel_qq_managed_auth(self) -> None:
        self._close_qq_managed_auth_dialog()
        self.page.run_task(self.dashboard.cancel_managed_authentication)

    def _supports_discord_managed_auth_reopen(self) -> bool:
        presentation = self._managed_authentication_presentation
        return bool(presentation is not None and presentation.browser_reopen_available)

    def _translation_enable_succeeded(self, dashboard: object, result: object) -> bool:
        _ = dashboard
        return getattr(result, "status", None) == "applied"

    def _start_discord_managed_auth(self) -> None:
        dialog = getattr(self, "_discord_managed_auth_dialog", None)
        raw_referral_id = getattr(dialog, "referral_id", "")
        referral_id = (
            raw_referral_id if isinstance(raw_referral_id, str) and raw_referral_id else None
        )
        set_waiting = getattr(dialog, "set_waiting", None)
        if callable(set_waiting):
            set_waiting()

        async def _task() -> None:
            try:
                result = await self.dashboard.start_discord_managed_authentication(
                    StartDiscordManagedAuthentication(referral_id)
                )
                self._managed_authentication_presentation = result.presentation
                if result.status != ManagedAuthenticationStatus.APPLIED:
                    return
                enable_result = await self.dashboard.set_translation_enabled(
                    SetTranslationEnabled(True)
                )
                if not self._translation_enable_succeeded(self.dashboard, enable_result):
                    return
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Discord managed auth task failed")
                return
            self._close_discord_managed_auth_dialog()
            self._show_snackbar(t("discord_auth.success"), COLOR_SUCCESS)
            if self._managed_authentication_presentation.referral_bonus_applied:
                self._show_snackbar(t("discord_auth.referral_reward_applied"), COLOR_SUCCESS)
            self._set_dashboard_translation_visual_state(True)

        self.page.run_task(_task)

    def _reopen_discord_managed_auth_browser(self) -> None:
        async def _task() -> None:
            result = await self.dashboard.reopen_discord_managed_authentication()
            self._managed_authentication_presentation = result.presentation

        self.page.run_task(_task)

    def _cancel_discord_managed_auth(self) -> None:
        self._close_discord_managed_auth_dialog()
        self.page.run_task(self.dashboard.cancel_managed_authentication)

    def _on_discord_managed_auth_byok(self) -> None:
        self._start_settings_pkce("discord_auth")

    def _on_founder_letter_connect(self) -> None:
        self._start_settings_pkce("letter")

    def _start_settings_pkce(self, launch_source: str) -> None:
        self.view_settings.request_pkce(launch_source)

    def _on_founder_letter_contact(self) -> None:
        webbrowser.open(FOUNDER_CONTACT_URL)

    def _on_founder_letter_readme(self) -> None:
        webbrowser.open(founder_readme_url_for_locale(get_locale()))

    def show_founder_letter_dialog(self) -> None:
        self._mark_launch_high_priority_feedback_shown("usage_exhaustion")
        dialog = FounderLetterDialog(self.page, on_readme=self._on_founder_letter_readme)
        self._founder_letter_dialog = dialog
        dialog.open()

    def _show_snackbar(self, message: str, bgcolor, duration: int = 4000) -> None:
        """Show a snackbar above the bottom nav."""
        snackbar = ft.SnackBar(
            ft.Text(message, size=18, color=ft.Colors.WHITE),
            bgcolor=bgcolor,
            duration=duration,
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.only(bottom=90),
            padding=20,
        )
        self._mark_launch_high_priority_feedback_shown("snackbar", snackbar)
        self.page.open(snackbar)

    def show_snackbar(self, message: str, bgcolor) -> None:
        self._show_snackbar(message, bgcolor)

    def clear_managed_auth_pending_state(self) -> None:
        self.page.run_task(self.dashboard.cancel_managed_authentication)

    def get_event_language_codes(self) -> tuple[str | None, str | None]:
        snapshot = self._dashboard_snapshot
        if snapshot is None:
            return None, None
        return snapshot.settings.languages.source, snapshot.settings.languages.target

    def is_event_translation_enabled(self) -> bool:
        snapshot = self._dashboard_snapshot
        return bool(snapshot is not None and snapshot.translation_enabled)

    def get_event_stt_state(self) -> object | None:
        snapshot = self._dashboard_snapshot
        return None if snapshot is None else snapshot.self_stt_state

    def on_github_star_translation_success(self) -> None:
        self.dashboard.observe_github_star_translation_success()

    def on_telemetry_translation_success(self) -> None:
        async def _task() -> None:
            await self.dashboard.record_telemetry_translation_success_day()

        self._queue_settings_mutation_task(_task)

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


async def main_gui(
    page: ft.Page,
    *,
    config_path,
    debug_ui_preview: bool = False,
    allow_stable_settings_import: bool = False,
    overlay_commands: OverlayApplicationCommandPort | None = None,
    overlay_application_state: OverlayApplicationStatePort | None = None,
    overlay_ui_projection: object | None = None,
    vrc_audio_gate: AudioCaptureGatePort | None = None,
    surface_runtime_transactions: SurfaceRuntimeTransactionPort | None = None,
    application_runtime_host: object | None = None,
    application_adapters: object | None = None,
    ui_settings: UiSettingsApplication,
    dashboard: DashboardApplicationPort,
    defer_startup: bool = False,
):
    parameters = inspect.signature(TranslatorApp).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    candidates = {
        "config_path": config_path,
        "debug_ui_preview": debug_ui_preview,
        "overlay_commands": overlay_commands,
        "overlay_application_state": overlay_application_state,
        "overlay_ui_projection": overlay_ui_projection,
        "surface_runtime_transactions": surface_runtime_transactions,
        "vrc_audio_gate": vrc_audio_gate,
        "application_runtime_host": application_runtime_host,
        "application_adapters": application_adapters,
        "ui_settings": ui_settings,
        "dashboard": dashboard,
    }
    app_kwargs = {
        name: value for name, value in candidates.items() if name in parameters or accepts_kwargs
    }
    if "allow_stable_settings_import" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        app_kwargs["allow_stable_settings_import"] = allow_stable_settings_import
    app = TranslatorApp(page, **app_kwargs)
    if not defer_startup:
        await complete_main_gui_startup(app, page)
    return app


async def complete_main_gui_startup(app: TranslatorApp, page: ft.Page) -> None:
    await app.view_settings.load()

    # Check for updates in background
    update_kwargs = {"log_detailed": app._log_detailed}
    try:
        update_parameters = inspect.signature(_check_and_notify_update).parameters
    except (TypeError, ValueError):
        update_parameters = {}
    if "on_launch_snackbar_shown" in update_parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in update_parameters.values()
    ):
        update_kwargs["on_launch_snackbar_shown"] = (
            lambda snackbar: app._mark_launch_high_priority_feedback_shown("update", snackbar)
        )
    await _check_and_notify_update(page, **update_kwargs)

    show_github_star_prompt = getattr(app, "maybe_show_github_star_prompt_after_launch", None)
    if callable(show_github_star_prompt):
        await show_github_star_prompt()


async def _check_and_notify_update(
    page: ft.Page,
    log_detailed=None,
    on_launch_snackbar_shown=None,
) -> None:
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
        if callable(on_launch_snackbar_shown):
            on_launch_snackbar_shown(snackbar)

    except Exception as exc:
        message = f"[Update] Check notification failed: {exc}"
        if callable(log_detailed):
            log_detailed(message)
            return
        logger.debug(message)
