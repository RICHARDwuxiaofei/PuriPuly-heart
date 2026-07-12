from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.runtime_resources import RuntimeResourceAction
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.config.overlay_calibration import OverlayCalibration
from puripuly_heart.core.messages import RuntimeApplyResult, TransactionResult

RuntimeMutationSurface = Literal[
    "translation_provider",
    "stt_language_audio",
    "overlay_osc_output",
    "ui_prompt_clipboard_state",
    "openrouter_pkce",
    "managed_legacy",
]
RuntimeMutationCause = Literal["settings_surface", "pkce", "managed_legacy"]
ProviderIngressPolicy = Literal["active", "staged", "inactive"]
ManagedLegacyDirective = Literal["none", "managed_release_rebuild_c4"]
RuntimeOperation = Literal[
    "provider_activation",
    "translation_policy",
    "language_runtime_clear",
    "audio_vad",
    "overlay_osc",
    "locale_ui_projection",
    "prompt_clipboard",
    "dashboard_retry_facts",
]


@dataclass(frozen=True, slots=True)
class RuntimeMutationProvenance:
    surface: RuntimeMutationSurface
    cause: RuntimeMutationCause
    reason: str | None
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class RuntimeOperationalSnapshot:
    translation_enabled: bool
    self_stt_enabled: bool
    self_stt_running: bool
    self_stt_staged: bool
    peer_stt_enabled: bool
    peer_stt_running: bool
    peer_stt_staged: bool
    llm_available: bool
    llm_retry_pending: bool
    self_stt_available: bool
    self_stt_retry_pending: bool
    peer_stt_available: bool
    peer_stt_retry_pending: bool


@dataclass(frozen=True, slots=True)
class LanguageRuntimeDirective:
    operation: Literal["language_runtime_clear"]
    source_language: str
    target_language: str
    peer_source_language: str
    peer_target_language: str


@dataclass(frozen=True, slots=True)
class AudioVadDirective:
    operation: Literal["audio_vad"]
    input_host_api: str
    input_device: str
    output_device: str
    ring_buffer_ms: int
    self_vad_threshold: float
    peer_vad_threshold: float
    peer_vad_hangover_ms: int
    peer_vad_pre_roll_ms: int


@dataclass(frozen=True, slots=True)
class OverlayOscDirective:
    operation: Literal["overlay_osc"]
    overlay_target: str
    show_translation: bool
    show_peer_original: bool
    osc_host: str
    osc_port: int
    chatbox_address: str
    chatbox_send: bool
    chatbox_clear: bool
    chatbox_max_chars: int
    vrc_mic_intercept: bool
    chatbox_include_source: bool
    calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    desktop_overlay_options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocaleUiProjectionDirective:
    operation: Literal["locale_ui_projection"]
    locale: str


@dataclass(frozen=True, slots=True)
class PromptClipboardDirective:
    operation: Literal["prompt_clipboard"]
    system_prompt: str
    clipboard_auto_translate_enabled: bool


@dataclass(frozen=True, slots=True)
class DashboardRetryFactsDirective:
    operation: Literal["dashboard_retry_facts"]
    llm_available: bool
    llm_retry_pending: bool
    self_stt_available: bool
    self_stt_retry_pending: bool
    peer_stt_available: bool
    peer_stt_retry_pending: bool


@dataclass(frozen=True, slots=True)
class TranslationPolicyDirective:
    operation: Literal["translation_policy"]
    enabled: bool


RuntimeSyncDirective = (
    TranslationPolicyDirective
    | LanguageRuntimeDirective
    | AudioVadDirective
    | OverlayOscDirective
    | LocaleUiProjectionDirective
    | PromptClipboardDirective
    | DashboardRetryFactsDirective
)


@dataclass(frozen=True, slots=True)
class ProviderActivationDirective:
    llm: RuntimeResourceAction
    self_stt: RuntimeResourceAction
    peer_stt: RuntimeResourceAction
    self_stt_policy: ProviderIngressPolicy
    peer_stt_policy: ProviderIngressPolicy
    managed_legacy: ManagedLegacyDirective


@dataclass(frozen=True, slots=True)
class PostCommitRuntimePlan:
    before: SettingsCommitReceipt | None
    after: SettingsCommitReceipt
    operational: RuntimeOperationalSnapshot
    request: ResolvedRuntimeActivationRequest
    provenance: RuntimeMutationProvenance
    providers: ProviderActivationDirective
    synchronization: tuple[RuntimeSyncDirective, ...]


@dataclass(frozen=True, slots=True)
class PostCommitRuntimeExecutionResult:
    transaction: TransactionResult
    completed: tuple[RuntimeOperation, ...]
    failed: RuntimeOperation | None
    skipped: tuple[RuntimeOperation, ...]
    reconciliation_required: bool


class ProviderActivationPort(Protocol):
    async def activate_providers(
        self, request: ResolvedRuntimeActivationRequest, directive: ProviderActivationDirective
    ) -> RuntimeApplyResult: ...


class CommittedRuntimeSynchronizationPort(Protocol):
    async def synchronize_runtime(
        self,
        request: ResolvedRuntimeActivationRequest,
        directive: RuntimeSyncDirective,
        *,
        before: SettingsCommitReceipt | None,
        after: SettingsCommitReceipt,
        operational: RuntimeOperationalSnapshot,
    ) -> RuntimeApplyResult: ...


class SurfaceRuntimeTransactionPort(Protocol):
    async def apply_surface_runtime(
        self,
        *,
        before: SettingsCommitReceipt | None,
        after: SettingsCommitReceipt,
        provenance: RuntimeMutationProvenance,
        operational: RuntimeOperationalSnapshot,
    ) -> PostCommitRuntimeExecutionResult: ...


__all__ = [name for name in globals() if not name.startswith("_")]
