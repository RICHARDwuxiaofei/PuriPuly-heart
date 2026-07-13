from __future__ import annotations

from puripuly_heart.app.ports.application_settings import SettingChange, SettingsField
from puripuly_heart.app.ports.dashboard_application import (
    ChangeDashboardLanguages,
    DashboardCommandResult,
    DashboardCommandStatus,
    DashboardManagedAction,
    DashboardOverlayPeerPresentation,
    DashboardSnapshot,
    DashboardTogglePresentation,
    SelectCaptureTarget,
    SetManualInputActivity,
    SetOverlayEnabled,
    SetPeerEulaAccepted,
    SetPeerTranslationEnabled,
    SetRuntimeLoggingMode,
    SetSelfSttEnabled,
    SetTelemetryConsent,
    SetTranslationEnabled,
    SubmitManualSelfText,
    VerifyProviderCredential,
)
from puripuly_heart.app.ports.dashboard_presentation import DashboardPresentationEventContext
from puripuly_heart.app.ports.github_star_prompt_application import GithubStarPromptApplicationPort
from puripuly_heart.app.ports.managed_authentication_application import (
    ManagedAuthenticationApplicationPort,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)
from puripuly_heart.app.ports.overlay_application import (
    OverlayApplicationCommandPort,
    OverlayApplicationStatePort,
)
from puripuly_heart.app.ports.ui_settings import UiSettingsDelta
from puripuly_heart.app.services.application_runtime_host import ApplicationRuntimeHost
from puripuly_heart.app.services.ui_settings import UiSettingsApplication
from puripuly_heart.core.runtime.self_audio import SetSelfSTTEnabled


class DashboardApplication:
    def __init__(
        self,
        *,
        runtime_host: ApplicationRuntimeHost,
        ui_settings: UiSettingsApplication,
        runtime_logging,
        managed_authentication: ManagedAuthenticationApplicationPort,
        github_star_prompt: GithubStarPromptApplicationPort,
        telemetry_owner,
        overlay_commands: OverlayApplicationCommandPort,
        overlay_state: OverlayApplicationStatePort,
        canonical_settings,
        debug_audio_faults_enabled: bool = False,
    ) -> None:  # noqa: ANN001
        self._runtime_host = runtime_host
        self._ui_settings = ui_settings
        self._runtime_logging = runtime_logging
        self._managed_authentication = managed_authentication
        self._github_star_prompt = github_star_prompt
        self._telemetry_owner = telemetry_owner
        self._overlay_commands = overlay_commands
        self._overlay_state = overlay_state
        self._canonical_settings = canonical_settings
        self._debug_audio_faults_enabled = debug_audio_faults_enabled
        self._debug_capture_fault_profile = "none"
        self._debug_stt_fault_profile = "none"
        self._last_settings_snapshot = None

    async def snapshot(self) -> DashboardSnapshot:
        settings = await self._ui_settings.snapshot()
        operational_state = await self._ui_settings.operational_snapshot()
        operational_values = dict(operational_state.leaves)
        self._last_settings_snapshot = settings
        operational = self._runtime_host.runtime_operational_snapshot()
        self_state = self._runtime_host.snapshot()
        return DashboardSnapshot(
            settings=settings,
            translation_enabled=operational.translation_enabled,
            self_stt_enabled=operational.self_stt_enabled,
            self_stt_state=str(self_state.state.value),
            peer_translation_enabled=operational.peer_stt_enabled,
            overlay_enabled=settings.runtime.overlay_active,
            runtime_logging_mode=self.runtime_logging_mode,
            peer_eula_accepted=bool(
                operational_values.get(("peer_translation", "eula_accepted"), False)
            ),
            microphone_test_active=settings.runtime.microphone_test_active,
            telemetry_consent=settings.ui_clipboard_telemetry.telemetry_consent,
            telemetry_endpoint_configured=(
                settings.ui_clipboard_telemetry.telemetry_endpoint_configured
            ),
            telemetry_last_delivery_status=(settings.ui_clipboard_telemetry.last_delivery_status),
            overlay_peer=self._overlay_peer_presentation(settings, operational),
        )

    def _overlay_peer_presentation(
        self, settings, operational  # noqa: ANN001
    ) -> DashboardOverlayPeerPresentation:
        overlay_state = settings.runtime.overlay_state
        overlay_intent = bool(settings.overlay.desktop_enabled or settings.runtime.overlay_active)
        overlay_warning = None
        if overlay_intent and overlay_state not in {"starting", "connected"}:
            overlay_warning = "overlay_failed" if overlay_state == "failed" else "overlay_required"
        peer_intent = operational.peer_stt_enabled
        peer_effective = operational.peer_stt_running
        peer_warning = None
        if peer_intent and not peer_effective:
            peer_warning = (
                "overlay_failed"
                if overlay_state == "failed"
                else "overlay_required" if overlay_state != "connected" else "runtime_unavailable"
            )
        return DashboardOverlayPeerPresentation(
            overlay=DashboardTogglePresentation(
                overlay_intent,
                overlay_state == "connected",
                True,
                (
                    "off"
                    if not overlay_intent
                    else "on" if overlay_state in {"starting", "connected"} else "warning"
                ),
                overlay_warning,
                settings.runtime.overlay_failure_code,
            ),
            peer=DashboardTogglePresentation(
                peer_intent,
                peer_effective,
                overlay_state == "connected" or peer_intent,
                "off" if not peer_intent else "on" if peer_effective else "warning",
                peer_warning,
                settings.runtime.overlay_failure_code if peer_warning == "overlay_failed" else None,
            ),
        )

    @property
    def event_queue(self):  # noqa: ANN201
        return self._runtime_host.ui_event_queue

    @property
    def output_runtime(self):  # noqa: ANN201
        return self._runtime_host.output_runtime

    @property
    def runtime_logging(self):  # noqa: ANN201
        return self._runtime_logging

    def current_event_context(self) -> DashboardPresentationEventContext:
        settings = self._last_settings_snapshot
        languages = settings.languages if settings is not None else None
        operational = self._runtime_host.runtime_operational_snapshot()
        return DashboardPresentationEventContext(
            source_language=None if languages is None else languages.source_language,
            target_language=None if languages is None else languages.target_language,
            translation_enabled=operational.translation_enabled,
            self_stt_state=self._runtime_host.snapshot().state.value,
            runtime_logging_mode=self.runtime_logging_mode,
        )

    def clear_managed_auth_pending(self) -> None:
        self._managed_authentication.clear_pending()

    def observe_translation_success(self) -> None:
        self._github_star_prompt.observe_translation_success()

    async def record_translation_success(self) -> None:
        self.observe_translation_success()
        await self.record_telemetry_translation_success_day()

    def create_presentation_lifecycle(self, *, view, bridge_factory):  # noqa: ANN001, ANN201
        from puripuly_heart.app.services.dashboard_presentation_lifecycle import (
            DashboardPresentationLifecycle,
        )

        return DashboardPresentationLifecycle(
            view=view,
            bridge_factory=bridge_factory,
            event_queue=self.event_queue,
            context=self,
            output_runtime=self.output_runtime,
            runtime_logging=self._runtime_logging,
        )

    @property
    def runtime_logging_mode(self) -> str:
        mode = getattr(self._runtime_logging, "mode", "basic")
        return mode if isinstance(mode, str) else "basic"

    async def set_translation_enabled(self, command: SetTranslationEnabled):  # noqa: ANN201
        from puripuly_heart.app.ports.translation_application import (
            SetTranslationEnabled as Runtime,
        )

        return await self._runtime_host.set_translation_enabled(Runtime(command.enabled))

    async def set_self_stt_enabled(self, command: SetSelfSttEnabled):  # noqa: ANN201
        return await self._runtime_host.execute(
            SetSelfSTTEnabled(command.enabled, force_immediate=command.force_immediate)
        )

    async def set_overlay_enabled(self, command: SetOverlayEnabled) -> DashboardCommandResult:
        receipt = await self._canonical_settings()
        runtime = self._overlay_state.runtime_snapshot()
        self._overlay_commands.configure_intent(
            receipt.envelope,
            enabled=command.enabled,
            runtime_logging_mode=self.runtime_logging_mode,
            interaction_mode=runtime.interaction_mode,
        )
        try:
            await self._overlay_commands.set_enabled(command.enabled)
        except Exception:
            return await self._interaction_result(
                "failed", "overlay_lifecycle_failed", receipt=receipt
            )
        lifecycle = self._overlay_state.lifecycle_snapshot()
        effective = lifecycle.state.value in {"starting", "connected"}
        status = "applied" if effective == command.enabled else "failed"
        return await self._interaction_result(status, lifecycle.failure_reason, receipt=receipt)

    async def set_peer_translation_enabled(
        self, command: SetPeerTranslationEnabled
    ) -> DashboardCommandResult:
        if command.enabled:
            snapshot = await self.snapshot()
            if not snapshot.peer_eula_accepted:
                return DashboardCommandResult(
                    DashboardCommandStatus.REJECTED, snapshot, "peer_eula_required"
                )
            if snapshot.settings.runtime.overlay_state != "connected":
                return DashboardCommandResult(
                    DashboardCommandStatus.UNAVAILABLE, snapshot, "overlay_connected_required"
                )
            result = await self._runtime_host.resume_peer_stt()
            status = "applied" if result.status == "applied" else "failed"
        else:
            result = await self._runtime_host.pause_peer_stt()
            status = "applied" if result.status == "applied" else "failed"
        detail = "peer_runtime_reconciliation_required" if result.reconciliation_required else None
        return await self._interaction_result(status, detail, receipt=result.receipt)

    async def submit_manual_self_text(
        self, command: SubmitManualSelfText
    ) -> DashboardCommandResult:
        status = await self._runtime_host.submit_manual_self_text(command.text)
        return await self._interaction_result(status)

    def set_manual_input_activity(self, command: SetManualInputActivity) -> None:
        self._runtime_host.set_manual_input_activity(command.has_text)

    async def retry_capture(self):  # noqa: ANN201
        return await self._ui_settings.interactions.retry_capture()

    async def select_capture_target(self, command: SelectCaptureTarget):  # noqa: ANN201
        return await self._ui_settings.select_capture_target(
            command.target_id, command.expected_revision
        )

    async def change_languages(self, command: ChangeDashboardLanguages):  # noqa: ANN201
        change = command.change
        return await self._ui_settings.apply(
            UiSettingsDelta(
                command.expected_revision,
                (
                    SettingChange(SettingsField.SOURCE_LANGUAGE, change.source_code),
                    SettingChange(SettingsField.TARGET_LANGUAGE, change.target_code),
                    SettingChange(SettingsField.PEER_SOURCE_MODE, change.peer_source_mode),
                    SettingChange(
                        SettingsField.PEER_SOURCE_LANGUAGE, change.peer_source_code or None
                    ),
                    SettingChange(
                        SettingsField.PEER_TARGET_LANGUAGE, change.peer_target_code or None
                    ),
                ),
            )
        )

    async def set_peer_eula(self, command: SetPeerEulaAccepted) -> DashboardCommandResult:
        result = await self._ui_settings.set_peer_translation_eula(
            command.accepted, command.expected_revision
        )
        return await self._interaction_result(result.status, receipt=result.receipt)

    def set_runtime_logging_mode(self, command: SetRuntimeLoggingMode) -> None:
        setter = getattr(self._runtime_logging, "set_mode", None)
        if callable(setter):
            setter(command.mode)

    async def set_telemetry_consent(self, command: SetTelemetryConsent):  # noqa: ANN201
        return await self._ui_settings.interactions.set_telemetry_consent(command.consent)

    async def verify_provider(self, command: VerifyProviderCredential):  # noqa: ANN201
        return await self._ui_settings.interactions.verify_provider(
            command.provider, command.secret_key
        )

    async def microphone_test(self, start: bool):  # noqa: ANN201
        return await self._ui_settings.interactions.microphone_test(start)

    async def managed_action(self, command: DashboardManagedAction):  # noqa: ANN201
        return await self._ui_settings.interactions.managed_action(command.action)

    async def _interaction_result(
        self,
        status: str,
        detail_code: str | None = None,
        receipt=None,  # noqa: ANN001
    ) -> DashboardCommandResult:
        mapped = {
            "applied": DashboardCommandStatus.APPLIED,
            "cancelled": DashboardCommandStatus.CANCELLED,
            "unavailable": DashboardCommandStatus.UNAVAILABLE,
            "rejected": DashboardCommandStatus.REJECTED,
        }.get(status, DashboardCommandStatus.FAILED)
        return DashboardCommandResult(mapped, await self.snapshot(), detail_code, receipt)

    async def close(self) -> None:
        await self._managed_authentication.close()
        await self._github_star_prompt.close()

    async def managed_authentication_presentation(self):  # noqa: ANN201
        return await self._managed_authentication.presentation()

    def subscribe_managed_authentication_presentation(self, listener):  # noqa: ANN001, ANN201
        return self._managed_authentication.subscribe_presentation(listener)

    async def start_discord_managed_authentication(
        self, command: StartDiscordManagedAuthentication
    ):  # noqa: ANN201
        return await self._managed_authentication.start_discord(command)

    async def start_qq_managed_authentication(
        self, command: StartQqManagedAuthentication
    ):  # noqa: ANN201
        return await self._managed_authentication.start_qq(command)

    async def reopen_discord_managed_authentication(self):  # noqa: ANN201
        return await self._managed_authentication.reopen_discord_browser()

    async def cancel_managed_authentication(self):  # noqa: ANN201
        return await self._managed_authentication.cancel()

    async def github_star_prompt_presentation(self):  # noqa: ANN201
        return await self._github_star_prompt.presentation()

    async def record_github_star_eligible_launch(self):  # noqa: ANN201
        return await self._github_star_prompt.record_eligible_launch()

    async def run_delayed_github_star_launch(self, delay_s: float):  # noqa: ANN201
        return await self._github_star_prompt.run_delayed_launch(delay_s)

    async def cancel_github_star_launch(self) -> None:
        await self._github_star_prompt.cancel_launch()

    async def record_github_star_opened(self):  # noqa: ANN201
        return await self._github_star_prompt.record_opened()

    async def record_github_star_clicked(self):  # noqa: ANN201
        return await self._github_star_prompt.record_clicked()

    def observe_github_star_translation_success(self) -> bool:
        return self._github_star_prompt.observe_translation_success()

    def log_basic(self, message: str, *, level: int) -> None:
        self._runtime_logging.log_basic(message, level=level)

    def log_detailed(self, message: str, *, level: int) -> None:
        self._runtime_logging.log_detailed(message, level=level)

    async def record_telemetry_translation_success_day(self):  # noqa: ANN201
        from puripuly_heart.core.telemetry import (
            TranslationSuccessTelemetryResult,
            TranslationSuccessTelemetryService,
        )

        snapshot = await self._telemetry_owner.load()
        if snapshot is None:
            return TranslationSuccessTelemetryResult("state_load_failed")
        release = await self._runtime_host.resolve_managed_release_service()
        client = None if release is None else getattr(release, "client", None)
        service = TranslationSuccessTelemetryService(client)
        return await service.record_translation_success_day(
            snapshot,
            persist_sent_date=self._telemetry_owner.mark_translation_success_date_sent,
        )

    def cycle_debug_capture_fault_profile(self) -> str:
        profiles = (
            "none",
            "capture_silent_first_channel",
            "capture_attenuate_40db",
            "capture_near_silence_noise",
            "capture_buffer_dropouts",
        )
        if not self._debug_audio_faults_enabled:
            return "none"
        index = (profiles.index(self._debug_capture_fault_profile) + 1) % len(profiles)
        self._debug_capture_fault_profile = profiles[index]
        self._sync_debug_audio_faults()
        return self._debug_capture_fault_profile

    def cycle_debug_stt_fault_profile(self) -> str:
        profiles = ("none", "stt_input_low_snr_vad_pass")
        if not self._debug_audio_faults_enabled:
            return "none"
        index = (profiles.index(self._debug_stt_fault_profile) + 1) % len(profiles)
        self._debug_stt_fault_profile = profiles[index]
        self._sync_debug_audio_faults()
        return self._debug_stt_fault_profile

    def clear_debug_audio_fault_profiles(self) -> None:
        self._debug_capture_fault_profile = "none"
        self._debug_stt_fault_profile = "none"
        self._sync_debug_audio_faults()

    def _sync_debug_audio_faults(self) -> None:
        self._runtime_host.set_debug_audio_faults(
            capture=self._debug_capture_fault_profile,
            stt=self._debug_stt_fault_profile,
        )


__all__ = ["DashboardApplication"]
