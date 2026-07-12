from __future__ import annotations

from typing import Awaitable, TypeVar

from puripuly_heart.app.ports.application_settings import SecretMetadata
from puripuly_heart.app.ports.ui_settings import (
    AudioDeviceQueryResult,
    AudioSettingsSnapshot,
    CaptureDiagnosticReason,
    CaptureRetryResult,
    CaptureRetryStatus,
    CaptureTargetSnapshot,
    CredentialMetadataSnapshot,
    InteractionResult,
    InteractionStatus,
    LanguageSettingsSnapshot,
    ManagedAction,
    ManagedActionResult,
    ManagedActionStatus,
    ManagedPresentation,
    MicrophoneTestResult,
    MicrophoneTestStatus,
    OscOutputSettingsSnapshot,
    OverlayAction,
    OverlaySettingsSnapshot,
    PkceStartRequest,
    PromptSettingsSnapshot,
    ProviderSettingsSnapshot,
    ProviderVerificationSnapshot,
    RuntimeFacts,
    SttSettingsSnapshot,
    TelemetryPresentation,
    TranslationSettingsSnapshot,
    UiClipboardTelemetrySnapshot,
    UiSettingsApplied,
    UiSettingsDelta,
    UiSettingsSnapshot,
)

T = TypeVar("T")


def _snapshot(revision: str = "debug-0") -> UiSettingsSnapshot:
    return UiSettingsSnapshot(
        TranslationSettingsSnapshot(model="gemma4", connection="managed"),
        ProviderSettingsSnapshot(),
        PromptSettingsSnapshot(system_prompt=""),
        SttSettingsSnapshot(self_provider="deepgram", peer_provider="deepgram"),
        LanguageSettingsSnapshot(source="auto", target="en"),
        AudioSettingsSnapshot(),
        OverlaySettingsSnapshot(target="steamvr"),
        OscOutputSettingsSnapshot(host="127.0.0.1", port=9000, send=False),
        UiClipboardTelemetrySnapshot(locale="en", telemetry_consent="unset"),
        CredentialMetadataSnapshot(),
        (),
        CaptureTargetSnapshot(status="unavailable"),
        ManagedPresentation(connection_state="unavailable"),
        RuntimeFacts(),
        revision,
        "debug-operational-0",
    )


class InertDebugUiSettingsInteractions:
    async def runtime_facts(self) -> RuntimeFacts:
        return RuntimeFacts()

    async def telemetry_presentation(self) -> TelemetryPresentation:
        return TelemetryPresentation()

    async def verification_presentation(self) -> tuple[ProviderVerificationSnapshot, ...]:
        return ()

    async def secret_metadata(self, key: str) -> SecretMetadata:
        raise RuntimeError(f"debug secret access unavailable: {key}")

    async def set_secret(self, key: str, value: str) -> SecretMetadata:
        _ = (key, value)
        raise RuntimeError("debug secret mutation unavailable")

    async def clear_secret(self, key: str) -> SecretMetadata:
        _ = key
        raise RuntimeError("debug secret mutation unavailable")

    async def verify_provider(self, provider: str, secret_key: str) -> ProviderVerificationSnapshot:
        _ = secret_key
        return ProviderVerificationSnapshot(provider, "unavailable", "debug_preview")

    async def start_pkce(self, request: PkceStartRequest) -> InteractionResult:
        _ = request
        return InteractionResult(InteractionStatus.UNAVAILABLE, "debug_preview")

    async def reopen_pkce(self) -> InteractionResult:
        return InteractionResult(InteractionStatus.UNAVAILABLE, "debug_preview")

    async def cancel_pkce(self) -> InteractionResult:
        return InteractionResult(InteractionStatus.UNAVAILABLE, "debug_preview")

    async def query_audio_devices(self, host_api: str = "") -> AudioDeviceQueryResult:
        _ = host_api
        return AudioDeviceQueryResult(InteractionStatus.UNAVAILABLE, detail_code="debug_preview")

    async def capture_targets(self, selected_id: str | None = None) -> CaptureTargetSnapshot:
        _ = selected_id
        return CaptureTargetSnapshot(status="unavailable")

    async def retry_capture(self) -> CaptureRetryResult:
        return CaptureRetryResult(
            CaptureRetryStatus.NOT_APPLICABLE,
            CaptureDiagnosticReason.NOT_APPLICABLE,
            "debug-0",
        )

    async def set_telemetry_consent(self, consent: str) -> InteractionResult:
        _ = consent
        return InteractionResult(InteractionStatus.UNAVAILABLE, "debug_preview")

    async def overlay_action(self, action: OverlayAction) -> InteractionResult:
        _ = action
        return InteractionResult(InteractionStatus.UNAVAILABLE, "debug_preview")

    async def microphone_test(self, start: bool) -> MicrophoneTestResult:
        _ = start
        return MicrophoneTestResult(MicrophoneTestStatus.UNAVAILABLE, "debug_preview")

    async def managed_action(self, action: ManagedAction) -> ManagedActionResult:
        _ = action
        return ManagedActionResult(
            ManagedActionStatus.UNAVAILABLE,
            ManagedPresentation(connection_state="unavailable"),
            "debug_preview",
        )

    async def managed_presentation(self) -> ManagedPresentation:
        return ManagedPresentation(connection_state="unavailable")

    async def close(self) -> None:
        return None


class InertDebugUiSettingsApplication:
    def __init__(self) -> None:
        self.interactions = InertDebugUiSettingsInteractions()
        self._snapshot = _snapshot()

    async def start(self) -> None:
        return None

    async def snapshot(self) -> UiSettingsSnapshot:
        return self._snapshot

    async def apply(self, delta: UiSettingsDelta) -> UiSettingsApplied:
        _ = delta
        return UiSettingsApplied(self._snapshot, ())

    async def select_capture_target(
        self, target_id: str, expected_revision: str
    ) -> UiSettingsApplied:
        _ = (target_id, expected_revision)
        return UiSettingsApplied(self._snapshot, ())

    def run_interaction(self, operation: Awaitable[T]) -> Awaitable[T]:
        return operation

    async def close(self) -> None:
        return None
