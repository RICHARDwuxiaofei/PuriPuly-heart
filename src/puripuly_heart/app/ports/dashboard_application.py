from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from puripuly_heart.app.language_selection import LanguageSelectionChange
from puripuly_heart.app.ports.github_star_prompt_application import (
    GithubStarPromptPresentation,
    GithubStarPromptResult,
)
from puripuly_heart.app.ports.managed_authentication_application import (
    ManagedAuthenticationPresentation,
    ManagedAuthenticationResult,
    StartDiscordManagedAuthentication,
    StartQqManagedAuthentication,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.ports.translation_application import TranslationCommandResult
from puripuly_heart.app.ports.ui_settings import (
    CaptureRetryResult,
    InteractionResult,
    ManagedAction,
    ManagedActionResult,
    MicrophoneTestResult,
    ProviderVerificationSnapshot,
    UiSettingsResult,
    UiSettingsSnapshot,
)
from puripuly_heart.core.runtime.self_audio import SelfChannelCommandResult
from puripuly_heart.core.telemetry import TranslationSuccessTelemetryResult


class DashboardCommandStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DashboardTogglePresentation:
    intent_enabled: bool
    effective_enabled: bool
    action_enabled: bool
    state: str
    warning_reason: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardOverlayPeerPresentation:
    overlay: DashboardTogglePresentation
    peer: DashboardTogglePresentation


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    settings: UiSettingsSnapshot
    translation_enabled: bool
    self_stt_enabled: bool
    self_stt_state: str
    peer_translation_enabled: bool
    overlay_enabled: bool
    runtime_logging_mode: str
    peer_eula_accepted: bool
    microphone_test_active: bool
    telemetry_consent: str
    telemetry_endpoint_configured: bool
    telemetry_last_delivery_status: str | None
    overlay_peer: DashboardOverlayPeerPresentation


@dataclass(frozen=True, slots=True)
class DashboardCommandResult:
    status: DashboardCommandStatus
    snapshot: DashboardSnapshot
    detail_code: str | None = None
    receipt: SettingsCommitReceipt | None = None


@dataclass(frozen=True, slots=True)
class SetTranslationEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetSelfSttEnabled:
    enabled: bool
    force_immediate: bool = False


@dataclass(frozen=True, slots=True)
class SetPeerTranslationEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetOverlayEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class SetManualInputActivity:
    has_text: bool


@dataclass(frozen=True, slots=True)
class SubmitManualSelfText:
    text: str


@dataclass(frozen=True, slots=True)
class SelectCaptureTarget:
    target_id: str
    expected_revision: str


@dataclass(frozen=True, slots=True)
class ChangeDashboardLanguages:
    change: LanguageSelectionChange
    expected_revision: str


@dataclass(frozen=True, slots=True)
class SetPeerEulaAccepted:
    accepted: bool
    expected_revision: str


@dataclass(frozen=True, slots=True)
class SetRuntimeLoggingMode:
    mode: str


@dataclass(frozen=True, slots=True)
class SetTelemetryConsent:
    consent: str


@dataclass(frozen=True, slots=True)
class VerifyProviderCredential:
    provider: str
    secret_key: str


@dataclass(frozen=True, slots=True)
class DashboardManagedAction:
    action: ManagedAction


class DashboardApplicationPort(Protocol):
    @property
    def runtime_logging_mode(self) -> str: ...

    async def snapshot(self) -> DashboardSnapshot: ...
    async def set_translation_enabled(
        self, command: SetTranslationEnabled
    ) -> TranslationCommandResult: ...
    async def set_self_stt_enabled(
        self, command: SetSelfSttEnabled
    ) -> SelfChannelCommandResult: ...
    async def set_peer_translation_enabled(
        self, command: SetPeerTranslationEnabled
    ) -> DashboardCommandResult: ...
    async def set_overlay_enabled(self, command: SetOverlayEnabled) -> DashboardCommandResult: ...
    async def submit_manual_self_text(
        self, command: SubmitManualSelfText
    ) -> DashboardCommandResult: ...
    def set_manual_input_activity(self, command: SetManualInputActivity) -> None: ...
    async def retry_capture(self) -> CaptureRetryResult: ...
    async def select_capture_target(self, command: SelectCaptureTarget) -> UiSettingsResult: ...
    async def change_languages(self, command: ChangeDashboardLanguages) -> UiSettingsResult: ...
    async def set_peer_eula(self, command: SetPeerEulaAccepted) -> DashboardCommandResult: ...
    def set_runtime_logging_mode(self, command: SetRuntimeLoggingMode) -> None: ...
    async def set_telemetry_consent(self, command: SetTelemetryConsent) -> InteractionResult: ...
    async def verify_provider(
        self, command: VerifyProviderCredential
    ) -> ProviderVerificationSnapshot: ...
    async def microphone_test(self, start: bool) -> MicrophoneTestResult: ...
    async def managed_action(self, command: DashboardManagedAction) -> ManagedActionResult: ...
    async def managed_authentication_presentation(
        self,
    ) -> ManagedAuthenticationPresentation: ...
    def subscribe_managed_authentication_presentation(self, listener): ...  # noqa: ANN001, ANN201
    async def start_discord_managed_authentication(
        self, command: StartDiscordManagedAuthentication
    ) -> ManagedAuthenticationResult: ...
    async def start_qq_managed_authentication(
        self, command: StartQqManagedAuthentication
    ) -> ManagedAuthenticationResult: ...
    async def reopen_discord_managed_authentication(self) -> ManagedAuthenticationResult: ...
    async def cancel_managed_authentication(self) -> ManagedAuthenticationResult: ...
    async def github_star_prompt_presentation(self) -> GithubStarPromptPresentation: ...
    async def record_github_star_eligible_launch(self) -> GithubStarPromptResult: ...
    async def run_delayed_github_star_launch(self, delay_s: float) -> GithubStarPromptResult: ...
    async def cancel_github_star_launch(self) -> None: ...
    async def record_github_star_opened(self) -> GithubStarPromptResult: ...
    async def record_github_star_clicked(self) -> GithubStarPromptResult: ...
    def observe_github_star_translation_success(self) -> bool: ...
    def log_basic(self, message: str, *, level: int) -> None: ...
    def log_detailed(self, message: str, *, level: int) -> None: ...
    async def record_telemetry_translation_success_day(
        self,
    ) -> TranslationSuccessTelemetryResult: ...
    def cycle_debug_capture_fault_profile(self) -> str: ...
    def cycle_debug_stt_fault_profile(self) -> str: ...
    def clear_debug_audio_fault_profiles(self) -> None: ...
    def create_presentation_lifecycle(self, *, view, bridge_factory): ...  # noqa: ANN001, ANN201
    async def close(self) -> None: ...


__all__ = [
    "ChangeDashboardLanguages",
    "DashboardApplicationPort",
    "DashboardCommandResult",
    "DashboardCommandStatus",
    "DashboardManagedAction",
    "DashboardOverlayPeerPresentation",
    "DashboardSnapshot",
    "DashboardTogglePresentation",
    "SelectCaptureTarget",
    "SetManualInputActivity",
    "SetOverlayEnabled",
    "SetPeerEulaAccepted",
    "SetPeerTranslationEnabled",
    "SetRuntimeLoggingMode",
    "SetSelfSttEnabled",
    "SetTelemetryConsent",
    "SetTranslationEnabled",
    "SubmitManualSelfText",
    "VerifyProviderCredential",
]
