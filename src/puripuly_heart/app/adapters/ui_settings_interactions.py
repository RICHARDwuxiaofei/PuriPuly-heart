from __future__ import annotations

import asyncio
from dataclasses import replace

from puripuly_heart.app.ports.application_settings import (
    ClearSecretCommand,
    SecretMetadataQuery,
    SetSecretCommand,
)
from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceQueryResult,
    CaptureTargetOption,
    CaptureTargetSnapshot,
    InteractionResult,
    InteractionStatus,
    ManagedAction,
    ManagedActionResult,
    ManagedActionStatus,
    ManagedPresentation,
    MicrophoneTestResult,
    OverlayAction,
    PkceStartRequest,
    ProviderVerificationSnapshot,
)
from puripuly_heart.app.services.openrouter_pkce_owner import (
    CancelOpenRouterPkce,
    ReopenOpenRouterPkce,
    StartOpenRouterPkce,
)


class ProductionUiSettingsInteractions:
    def __init__(
        self,
        canonical_commands,
        *,
        provider_verifier=None,
        runtime_host=None,
        telemetry_owner=None,
        overlay=None,
        microphone=None,
        persistence=None,
        owned_services=(),
        capture_candidates=None,
        audio_devices=None,
    ) -> None:  # noqa: ANN001
        self._commands = canonical_commands
        self._provider_verifier = provider_verifier
        self._runtime_host = runtime_host
        self._telemetry_owner = telemetry_owner
        self._overlay = overlay
        self._microphone = microphone
        self._persistence = persistence
        self._owned_services = tuple(owned_services)
        self._capture_candidates = capture_candidates
        self._audio_devices = audio_devices
        self._verification_state: dict[str, ProviderVerificationSnapshot] = {}

    async def runtime_facts(self):  # noqa: ANN201
        from puripuly_heart.app.ports.ui_settings import RuntimeFacts

        operational = (
            None
            if self._runtime_host is None
            else self._runtime_host.runtime_operational_snapshot()
        )
        overlay_state = "off"
        overlay_failure = None
        if self._overlay is not None:
            snapshot = self._overlay.lifecycle_snapshot()
            overlay_state = snapshot.state
            overlay_failure = snapshot.failure_reason
        return RuntimeFacts(
            overlay_active=overlay_state in {"starting", "connected"},
            peer_active=bool(operational and operational.peer_stt_running),
            overlay_state=overlay_state,
            overlay_failure_code=overlay_failure,
            microphone_test_active=bool(self._microphone and self._microphone.active),
        )

    async def telemetry_presentation(self):  # noqa: ANN201
        from puripuly_heart.app.ports.ui_settings import TelemetryPresentation

        if self._telemetry_owner is None:
            return TelemetryPresentation()
        snapshot = await self._telemetry_owner.load()
        managed = self._cached_managed_service()
        client = None if managed is None else managed.client
        return TelemetryPresentation(
            consent="unset" if snapshot is None else snapshot.consent,
            endpoint_configured=bool(
                client is not None
                and callable(getattr(client, "record_translation_success_day", None))
            ),
        )

    async def verification_presentation(self) -> tuple[ProviderVerificationSnapshot, ...]:
        return tuple(self._verification_state[key] for key in sorted(self._verification_state))

    async def secret_metadata(self, key: str):  # noqa: ANN201
        return await self._commands.secret_queries.secret_metadata(SecretMetadataQuery(key))

    async def set_secret(self, key: str, value: str):  # noqa: ANN201
        return await self._commands.secret_commands.set_secret(SetSecretCommand(key, value))

    async def clear_secret(self, key: str):  # noqa: ANN201
        return await self._commands.secret_commands.clear_secret(ClearSecretCommand(key))

    async def verify_provider(self, provider: str, secret_key: str) -> ProviderVerificationSnapshot:
        if self._provider_verifier is None:
            return ProviderVerificationSnapshot(
                provider, "unavailable", "verification_owner_unbound"
            )
        from puripuly_heart.app.ports.provider_verifier import ProviderVerificationRequest

        value = await self._commands.resolve_secret_value(secret_key)
        if value is None:
            return ProviderVerificationSnapshot(provider, "failed", "secret_missing")
        metadata = await self.secret_metadata(secret_key)
        result = await self._provider_verifier.verify_provider_secret(
            ProviderVerificationRequest(provider, secret_key, value, metadata.revision, {})
        )
        presentation = ProviderVerificationSnapshot(provider, result.status)
        self._verification_state[provider] = presentation
        return presentation

    async def start_pkce(self, request: PkceStartRequest) -> InteractionResult:
        if self._runtime_host is None or self._persistence is None:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "pkce_owner_unbound")
        receipt = await self._commands.current_receipt()
        if receipt.revision != request.expected_revision:
            return InteractionResult(InteractionStatus.FAILED, "revision_conflict")
        translation = replace(
            receipt.envelope.intent.translation,
            connection="openrouter",
            openrouter_model=request.model,
            openrouter_selected_source="byok",
            openrouter_selection_alias=request.selection_alias,
        )
        target = replace(
            receipt.envelope,
            intent=replace(receipt.envelope.intent, translation=translation),
        )
        result = await self._runtime_host.execute_openrouter_pkce(
            StartOpenRouterPkce(
                self._persistence.values_for(target),
                request.expected_revision,
                request.launch_source,
                operational=self._runtime_host.runtime_operational_snapshot(),
            )
        )
        status = (
            InteractionStatus.APPLIED
            if result.status in {"started", "succeeded", "committed_degraded"}
            else InteractionStatus.FAILED
        )
        return InteractionResult(status, result.status)

    async def reopen_pkce(self) -> InteractionResult:
        if self._runtime_host is None:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "pkce_owner_unbound")
        result = await self._runtime_host.execute_openrouter_pkce(ReopenOpenRouterPkce())
        return InteractionResult(
            InteractionStatus.APPLIED if result.status == "reopened" else InteractionStatus.FAILED,
            result.status,
        )

    async def cancel_pkce(self) -> InteractionResult:
        if self._runtime_host is None:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "pkce_owner_unbound")
        result = await self._runtime_host.execute_openrouter_pkce(CancelOpenRouterPkce())
        return InteractionResult(InteractionStatus.CANCELLED, result.status)

    async def query_audio_devices(self, host_api: str = "") -> AudioDeviceQueryResult:
        if self._audio_devices is None:
            return AudioDeviceQueryResult(
                InteractionStatus.UNAVAILABLE, detail_code="audio_enumerator_unbound"
            )
        return await self._audio_devices.query(host_api)

    async def capture_targets(self, selected_id: str | None = None) -> CaptureTargetSnapshot:
        from puripuly_heart.config.process_capture_resolution import ProcessCaptureResolver
        from puripuly_heart.core.audio.process_identity import PsutilCurrentUserProcessSnapshots

        try:
            candidates = (
                await self._capture_candidates()
                if self._capture_candidates is not None
                else await asyncio.to_thread(
                    ProcessCaptureResolver(PsutilCurrentUserProcessSnapshots()).enumerate_candidates
                )
            )
        except Exception:
            return CaptureTargetSnapshot(selected_id=selected_id, status="refresh_failed")
        options = [
            CaptureTargetOption(
                self._capture_target_id(item.target),
                "process",
                item.name,
                item.enabled,
            )
            for item in candidates
        ]
        if selected_id and all(item.target_id != selected_id for item in options):
            options.insert(
                0,
                CaptureTargetOption(
                    selected_id,
                    "process",
                    selected_id,
                    True,
                ),
            )
        options.sort(key=lambda item: not item.available)
        return CaptureTargetSnapshot(
            selected_id=selected_id,
            options=tuple(options),
            status="available",
        )

    async def retry_capture(self):  # noqa: ANN201
        if self._runtime_host is None:
            from puripuly_heart.app.ports.ui_settings import (
                CaptureDiagnosticReason,
                CaptureRetryResult,
                CaptureRetryStatus,
            )

            return CaptureRetryResult(
                CaptureRetryStatus.NOT_APPLICABLE,
                CaptureDiagnosticReason.NOT_APPLICABLE,
                "",
            )
        return await self._runtime_host.retry_peer_process_capture()

    @staticmethod
    def _capture_target_id(target) -> str:  # noqa: ANN001
        if target.kind == "discord":
            return f"process:discord:{target.discord_channel}"
        return f"process:{target.kind}:{target.executable_identity}"

    async def set_telemetry_consent(self, consent: str) -> InteractionResult:
        if self._telemetry_owner is None:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "telemetry_owner_unbound")
        result = await self._telemetry_owner.set_consent(consent)
        return InteractionResult(
            InteractionStatus.APPLIED if result is not None else InteractionStatus.FAILED
        )

    async def overlay_action(self, action: OverlayAction) -> InteractionResult:
        if self._overlay is None:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "overlay_owner_unbound")
        if action == OverlayAction.START:
            await self._overlay.startup()
        elif action == OverlayAction.STOP:
            await self._overlay.shutdown()
        else:
            return InteractionResult(InteractionStatus.UNAVAILABLE, "overlay_action_not_supported")
        return InteractionResult(InteractionStatus.APPLIED)

    async def microphone_test(self, start: bool) -> MicrophoneTestResult:
        if self._microphone is None:
            from puripuly_heart.app.ports.ui_settings import MicrophoneTestStatus

            return MicrophoneTestResult(
                MicrophoneTestStatus.UNAVAILABLE, "microphone_owner_unbound"
            )
        if start:
            return await self._microphone.start()
        return await self._microphone.stop()

    async def managed_action(self, action: ManagedAction) -> ManagedActionResult:
        if action != ManagedAction.REFRESH:
            service = self._cached_managed_service()
            current = self._managed_presentation_from(service)
            return ManagedActionResult(
                (
                    ManagedActionStatus.DEFERRED
                    if service is not None
                    else ManagedActionStatus.UNAVAILABLE
                ),
                current,
                (
                    "managed_transaction_deferred_to_c3"
                    if service is not None
                    else "managed_owner_unavailable"
                ),
            )
        if self._runtime_host is None:
            return ManagedActionResult(
                ManagedActionStatus.UNAVAILABLE,
                self._managed_presentation_from(None),
                "managed_owner_unavailable",
            )
        try:
            service = await self._runtime_host.resolve_managed_release_service()
            if service is None:
                return ManagedActionResult(
                    ManagedActionStatus.UNAVAILABLE,
                    self._managed_presentation_from(None),
                    "managed_owner_unavailable",
                )
            refresh = getattr(service, "refresh_presentation_state", None)
            if callable(refresh):
                await refresh(self._provider_verifier)
                return ManagedActionResult(
                    ManagedActionStatus.APPLIED,
                    self._managed_presentation_from(service),
                )
            result = await service.refresh_managed_status()
            presentation = ManagedPresentation(
                connection_state="connected" if result.succeeded else "failed",
                referral_id=result.referral_id,
                pass_status=_managed_pass_status(result.pass_status),
                available_actions=(ManagedAction.REFRESH,),
            )
            return ManagedActionResult(
                ManagedActionStatus.APPLIED if result.succeeded else ManagedActionStatus.FAILED,
                presentation,
                None if result.succeeded else "managed_refresh_failed",
            )
        except asyncio.CancelledError:
            return ManagedActionResult(
                ManagedActionStatus.CANCELLED,
                self._managed_presentation_from(self._cached_managed_service()),
                "managed_refresh_cancelled",
            )
        except Exception:
            return ManagedActionResult(
                ManagedActionStatus.FAILED,
                self._managed_presentation_from(self._cached_managed_service()),
                "managed_refresh_failed",
            )

    async def managed_presentation(self) -> ManagedPresentation:
        return self._managed_presentation_from(self._cached_managed_service())

    @staticmethod
    def _managed_presentation_from(service) -> ManagedPresentation:  # noqa: ANN001
        if service is None:
            return ManagedPresentation(connection_state="unavailable")
        return ManagedPresentation(
            connection_state="connected",
            trial_remaining_percent=getattr(service, "current_trial_remaining_percent", None),
            referral_id=service.managed_state.referral_id,
            pass_status=_managed_pass_status(getattr(service, "current_pass_status", None)),
            available_actions=(ManagedAction.REFRESH,),
        )

    def _cached_managed_service(self):  # noqa: ANN201
        if self._runtime_host is None:
            return None
        return self._runtime_host.managed_release_service

    async def close(self) -> None:
        failures = []
        for service in reversed(self._owned_services):
            try:
                await service.close()
            except BaseException as exc:
                failures.append(exc)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("UI settings interaction shutdown failed", failures)


__all__ = ["ProductionUiSettingsInteractions"]


def _managed_pass_status(value):  # noqa: ANN001, ANN202
    if value is None:
        return None
    count = getattr(value, "invite_count", None)
    limit = getattr(value, "invite_limit", None)
    if type(count) is int and type(limit) is int:
        return f"{count}/{limit}"
    return "available"
