from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Protocol, TypeAlias, TypeVar

from puripuly_heart.app.ports.application_settings import (
    ApplicationSettingsCommandPort,
    ApplicationSettingsQueryPort,
    CaptureTargetValue,
    DesktopOverlayValue,
    JsonScalarEntry,
    OperationalStateQueryPort,
    OverlayCalibrationValue,
    SecretCommandPort,
    SecretMetadata,
    SecretQueryPort,
    SettingChange,
    TranslationFallbackValue,
)
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt


class InteractionStatus(str, Enum):
    APPLIED = "applied"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OverlayAction(str, Enum):
    START = "start"
    STOP = "stop"
    RETRY = "retry"
    RESET_POSITION = "reset_position"


class ManagedAction(str, Enum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    REFRESH = "refresh"


class ManagedActionStatus(str, Enum):
    APPLIED = "applied"
    DEFERRED = "deferred"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaptureRetryStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class CaptureDiagnosticReason(str, Enum):
    TARGET_UNAVAILABLE = "target_unavailable"
    PROCESS_NOT_FOUND = "process_not_found"
    PROCESS_ACCESS_DENIED = "process_access_denied"
    PROCESS_INELIGIBLE = "process_ineligible"
    SETUP_FAILURE = "setup_failure"
    TARGET_EXITED = "target_exited"
    SOURCE_FAILURE = "source_failure"
    PROVIDER_FAILURE = "provider_failure"
    RUNTIME_FAILURE = "runtime_failure"
    SUCCESS = "success"
    NOT_APPLICABLE = "not_applicable"


class MicrophoneTestStatus(str, Enum):
    STARTED = "started"
    STOPPED = "stopped"
    ALREADY_ACTIVE = "already_active"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MicrophoneTestResult:
    status: MicrophoneTestStatus
    detail_code: str | None = None
    generation: int | None = None


@dataclass(frozen=True, slots=True)
class CaptureRetryResult:
    status: CaptureRetryStatus
    reason: CaptureDiagnosticReason
    canonical_revision: str
    process_detail: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationSettingsSnapshot:
    model: str | None = None
    connection: str | None = None
    connection_history: tuple[tuple[str, str], ...] = ()
    fallback_enabled: bool | None = None
    fallback_model: str | None = None
    fallback_connection: str | None = None
    fallback: TranslationFallbackValue | None = None

    def __post_init__(self) -> None:
        entries = tuple(self.connection_history)
        if any(
            not isinstance(entry, (tuple, list))
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
            for entry in entries
        ):
            raise TypeError("connection history must contain text pairs")
        object.__setattr__(
            self, "connection_history", tuple((entry[0], entry[1]) for entry in entries)
        )


@dataclass(frozen=True, slots=True)
class ProviderSettingsSnapshot:
    openrouter_model: str | None = None
    openrouter_routing_mode: str | None = None
    openrouter_selected_source: str | None = None
    qwen_model: str | None = None
    qwen_region: str | None = None
    cerebras_model: str | None = None
    local_backend: str | None = None
    local_base_url: str | None = None
    local_model: str | None = None
    concurrency_limit: int | None = None
    openrouter_provider_routing: str | None = None
    openrouter_selection_alias: str | None = None
    openrouter_broker_base_url: str | None = None
    local_extra_body: tuple[JsonScalarEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "local_extra_body", tuple(self.local_extra_body))


@dataclass(frozen=True, slots=True)
class PromptSettingsSnapshot:
    system_prompt: str | None = None
    origin: str = "canonical"
    provider_prompts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.provider_prompts)
        if any(
            not isinstance(entry, (tuple, list))
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
            for entry in entries
        ):
            raise TypeError("provider prompts must contain text pairs")
        object.__setattr__(
            self, "provider_prompts", tuple((entry[0], entry[1]) for entry in entries)
        )


@dataclass(frozen=True, slots=True)
class SttSettingsSnapshot:
    self_provider: str | None = None
    peer_provider: str | None = None
    drain_timeout_s: float | None = None
    vad_threshold: float | None = None
    low_latency: bool | None = None
    custom_vocabulary: bool | None = None
    custom_terms: tuple[VocabularyGroup, ...] = ()
    deepgram_model: str | None = None
    qwen_asr_model: str | None = None
    soniox_model: str | None = None
    soniox_endpoint: str | None = None
    soniox_keepalive_s: float | None = None
    soniox_trailing_silence_ms: int | None = None
    low_latency_hangover_ms: int | None = None
    low_latency_merge_gap_ms: int | None = None
    low_latency_retry_max: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "custom_terms", tuple(self.custom_terms))


@dataclass(frozen=True, slots=True)
class VocabularyGroup:
    language: str
    terms: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", tuple(self.terms))


@dataclass(frozen=True, slots=True)
class LanguageSettingsSnapshot:
    source: str | None = None
    target: str | None = None
    peer_source: str | None = None
    peer_target: str | None = None
    peer_source_mode: str | None = None
    peer_expected: tuple[str, ...] = ()
    recent_source: tuple[str, ...] = ()
    recent_target: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("peer_expected", "recent_source", "recent_target"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class AudioSettingsSnapshot:
    ring_buffer_ms: int | None = None
    input_host_api: str | None = None
    input_device: str | None = None
    desktop_output_device: str | None = None
    desktop_vad_threshold: float | None = None
    desktop_vad_hangover_ms: int | None = None
    desktop_vad_pre_roll_ms: int | None = None
    capture_target: CaptureTargetValue | None = None


@dataclass(frozen=True, slots=True)
class OverlaySettingsSnapshot:
    target: str | None = None
    show_translation: bool | None = None
    show_peer_original: bool | None = None
    desktop_enabled: bool | None = None
    calibration: OverlayCalibrationValue | None = None
    desktop: DesktopOverlayValue | None = None


@dataclass(frozen=True, slots=True)
class OscOutputSettingsSnapshot:
    host: str | None = None
    port: int | None = None
    chatbox_address: str | None = None
    send: bool | None = None
    clear: bool | None = None
    max_chars: int | None = None
    include_source: bool | None = None
    vrc_mic_intercept: bool | None = None


@dataclass(frozen=True, slots=True)
class UiClipboardTelemetrySnapshot:
    locale: str | None = None
    clipboard_auto_translate: bool | None = None
    integrated_context: bool | None = None
    telemetry_consent: str = "unset"
    telemetry_endpoint_configured: bool = False
    last_delivery_status: str | None = None
    secrets_backend: str | None = None
    secrets_encrypted_file_path: str | None = None


@dataclass(frozen=True, slots=True)
class CredentialMetadataSnapshot:
    entries: tuple[SecretMetadata, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class ProviderVerificationSnapshot:
    provider: str
    status: str
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureTargetOption:
    target_id: str
    kind: str
    display_name: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class CaptureTargetSnapshot:
    selected_id: str | None = None
    active_id: str | None = None
    options: tuple[CaptureTargetOption, ...] = ()
    status: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True, slots=True)
class ManagedPresentation:
    connection_state: str = "unknown"
    trial_remaining_percent: int | None = None
    referral_id: str | None = None
    pass_status: str | None = None
    available_actions: tuple[ManagedAction, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "available_actions", tuple(self.available_actions))


@dataclass(frozen=True, slots=True)
class ManagedActionResult:
    status: ManagedActionStatus
    presentation: ManagedPresentation
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeFacts:
    overlay_active: bool = False
    peer_active: bool = False
    overlay_state: str = "off"
    overlay_failure_code: str | None = None
    microphone_test_active: bool = False


@dataclass(frozen=True, slots=True)
class TelemetryPresentation:
    consent: str = "unset"
    endpoint_configured: bool = False
    last_delivery_status: str | None = None


@dataclass(frozen=True, slots=True)
class UiSettingsSnapshot:
    translation: TranslationSettingsSnapshot
    providers: ProviderSettingsSnapshot
    prompt: PromptSettingsSnapshot
    stt: SttSettingsSnapshot
    languages: LanguageSettingsSnapshot
    audio: AudioSettingsSnapshot
    overlay: OverlaySettingsSnapshot
    osc_output: OscOutputSettingsSnapshot
    ui_clipboard_telemetry: UiClipboardTelemetrySnapshot
    credentials: CredentialMetadataSnapshot
    verification: tuple[ProviderVerificationSnapshot, ...]
    capture: CaptureTargetSnapshot
    managed: ManagedPresentation
    runtime: RuntimeFacts
    canonical_revision: str
    operational_revision: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "verification", tuple(self.verification))

    def edit(self) -> UiSettingsEditDraft:
        return UiSettingsEditDraft(self.canonical_revision)


@dataclass(slots=True)
class UiSettingsEditDraft:
    expected_revision: str
    _changes: dict[str, SettingChange] = field(default_factory=dict, repr=False)

    def set(self, change: SettingChange) -> None:
        self._changes[change.field.value] = change

    def delta(self) -> UiSettingsDelta:
        return UiSettingsDelta(
            self.expected_revision, tuple(self._changes[key] for key in sorted(self._changes))
        )


@dataclass(frozen=True, slots=True)
class UiSettingsDelta:
    expected_revision: str
    changes: tuple[SettingChange, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))


@dataclass(frozen=True, slots=True)
class UiSurfaceOutcome:
    surface: str
    status: str
    receipt_revision: str | None
    receipt_reason: str | None
    receipt_correlation_id: str | None
    runtime_status: str | None
    runtime_completed: tuple[str, ...] = ()
    runtime_failed: str | None = None
    runtime_skipped: tuple[str, ...] = ()
    reconciliation_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_completed", tuple(self.runtime_completed))
        object.__setattr__(self, "runtime_skipped", tuple(self.runtime_skipped))


@dataclass(frozen=True, slots=True)
class UiSettingsApplied:
    snapshot: UiSettingsSnapshot
    outcomes: tuple[UiSurfaceOutcome, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


@dataclass(frozen=True, slots=True)
class UiSettingsConflict:
    snapshot: UiSettingsSnapshot
    expected_revision: str
    actual_revision: str
    outcomes: tuple[UiSurfaceOutcome, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


@dataclass(frozen=True, slots=True)
class UiSettingsDegraded:
    snapshot: UiSettingsSnapshot
    status: str
    outcomes: tuple[UiSurfaceOutcome, ...]
    reconciliation_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


UiSettingsResult: TypeAlias = UiSettingsApplied | UiSettingsConflict | UiSettingsDegraded


@dataclass(frozen=True, slots=True)
class InteractionResult:
    status: InteractionStatus
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class PkceStartRequest:
    selection_alias: str
    model: str
    expected_revision: str
    launch_source: str = "settings"


@dataclass(frozen=True, slots=True)
class AudioDeviceOption:
    device_id: str
    host_api: str
    display_name: str
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class AudioDeviceQueryResult:
    status: InteractionStatus
    host_apis: tuple[str, ...] = ()
    inputs: tuple[AudioDeviceOption, ...] = ()
    outputs: tuple[AudioDeviceOption, ...] = ()
    detail_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "host_apis", tuple(self.host_apis))
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "outputs", tuple(self.outputs))


class CanonicalUiCommands(Protocol):
    @property
    def settings_commands(self) -> ApplicationSettingsCommandPort: ...

    @property
    def settings_queries(self) -> ApplicationSettingsQueryPort: ...

    @property
    def operational_queries(self) -> OperationalStateQueryPort: ...

    @property
    def secret_queries(self) -> SecretQueryPort: ...

    @property
    def secret_commands(self) -> SecretCommandPort: ...

    async def current_receipt(self) -> SettingsCommitReceipt: ...

    async def resolve_secret_value(self, key: str) -> str | None: ...


class UiSettingsInteractionPort(Protocol):
    async def runtime_facts(self) -> RuntimeFacts: ...

    async def telemetry_presentation(self) -> TelemetryPresentation: ...

    async def verification_presentation(self) -> tuple[ProviderVerificationSnapshot, ...]: ...

    async def secret_metadata(self, key: str) -> SecretMetadata: ...
    async def set_secret(self, key: str, value: str) -> SecretMetadata: ...
    async def clear_secret(self, key: str) -> SecretMetadata: ...
    async def verify_provider(
        self, provider: str, secret_key: str
    ) -> ProviderVerificationSnapshot: ...
    async def start_pkce(self, request: PkceStartRequest) -> InteractionResult: ...

    async def reopen_pkce(self) -> InteractionResult: ...
    async def cancel_pkce(self) -> InteractionResult: ...
    async def query_audio_devices(self, host_api: str = "") -> AudioDeviceQueryResult: ...
    async def capture_targets(self, selected_id: str | None = None) -> CaptureTargetSnapshot: ...

    async def retry_capture(self) -> CaptureRetryResult: ...
    async def set_telemetry_consent(self, consent: str) -> InteractionResult: ...
    async def overlay_action(self, action: OverlayAction) -> InteractionResult: ...
    async def microphone_test(self, start: bool) -> MicrophoneTestResult: ...
    async def managed_action(self, action: ManagedAction) -> ManagedActionResult: ...
    async def managed_presentation(self) -> ManagedPresentation: ...
    async def close(self) -> None: ...


T = TypeVar("T")


class UiSettingsApplicationPort(Protocol):
    async def start(self) -> None: ...
    def run_interaction(self, operation: Awaitable[T]) -> "Awaitable[T]": ...
    async def close(self) -> None: ...


__all__ = [name for name in globals() if not name.startswith("_")]
