from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias

from puripuly_heart.core.messages import ErrorDiagnostics, UserMessageRef


class SettingsSurface(str, Enum):
    TRANSLATION_PROVIDER = "translation_provider"
    STT_LANGUAGE_AUDIO = "stt_language_audio"
    OVERLAY_OSC_OUTPUT = "overlay_osc_output"
    UI_PROMPT_CLIPBOARD = "ui_prompt_clipboard"


class SettingsField(str, Enum):
    TRANSLATION_MODEL = "translation.model"
    TRANSLATION_CONNECTION = "translation.connection"
    TRANSLATION_CONNECTION_HISTORY = "translation.connection_history"
    TRANSLATION_FALLBACK = "translation.fallback"
    OPENROUTER_LLM_MODEL = "openrouter.llm_model"
    OPENROUTER_ROUTING_MODE = "openrouter.routing_mode"
    OPENROUTER_PROVIDER_ROUTING = "openrouter.provider_routing"
    OPENROUTER_SELECTED_SOURCE = "openrouter.selected_source"
    OPENROUTER_SELECTION_ALIAS = "openrouter.selection_alias"
    OPENROUTER_BROKER_BASE_URL = "openrouter.broker_base_url"
    QWEN_LLM_MODEL = "qwen.llm_model"
    QWEN_REGION = "qwen.region"
    CEREBRAS_LLM_MODEL = "cerebras.llm_model"
    LOCAL_LLM_BACKEND = "local_llm.backend"
    LOCAL_LLM_BASE_URL = "local_llm.base_url"
    LOCAL_LLM_MODEL = "local_llm.model"
    LOCAL_LLM_EXTRA_BODY = "local_llm.extra_body"
    LLM_CONCURRENCY_LIMIT = "llm.concurrency_limit"
    PROVIDER_STT = "provider.stt"
    PROVIDER_PEER_STT = "provider.peer_stt"
    SOURCE_LANGUAGE = "languages.source_language"
    TARGET_LANGUAGE = "languages.target_language"
    PEER_SOURCE_LANGUAGE = "languages.peer_source_language"
    PEER_TARGET_LANGUAGE = "languages.peer_target_language"
    PEER_SOURCE_MODE = "languages.peer_source_mode"
    PEER_EXPECTED_LANGUAGES = "languages.peer_expected_languages"
    RECENT_SOURCE_LANGUAGES = "languages.recent_source_languages"
    RECENT_TARGET_LANGUAGES = "languages.recent_target_languages"
    AUDIO_RING_BUFFER_MS = "audio.ring_buffer_ms"
    AUDIO_INPUT_HOST_API = "audio.input_host_api"
    AUDIO_INPUT_DEVICE = "audio.input_device"
    DESKTOP_AUDIO_OUTPUT_DEVICE = "desktop_audio.output_device"
    DESKTOP_VAD_THRESHOLD = "desktop_audio.vad_speech_threshold"
    DESKTOP_VAD_HANGOVER_MS = "desktop_audio.vad_hangover_ms"
    DESKTOP_VAD_PRE_ROLL_MS = "desktop_audio.vad_pre_roll_ms"
    STT_DRAIN_TIMEOUT_S = "stt.drain_timeout_s"
    STT_VAD_THRESHOLD = "stt.vad_speech_threshold"
    STT_LOW_LATENCY_MODE = "stt.low_latency_mode"
    STT_LOW_LATENCY_VAD_HANGOVER_MS = "stt.low_latency_vad_hangover_ms"
    STT_LOW_LATENCY_MERGE_GAP_MS = "stt.low_latency_merge_gap_ms"
    STT_LOW_LATENCY_SPEC_RETRY_MAX = "stt.low_latency_spec_retry_max"
    STT_CUSTOM_VOCABULARY_ENABLED = "stt.custom_vocabulary_enabled"
    STT_CUSTOM_TERMS = "stt.custom_terms"
    DEEPGRAM_STT_MODEL = "deepgram_stt.model"
    QWEN_ASR_STT_MODEL = "qwen_asr_stt.model"
    SONIOX_STT_MODEL = "soniox_stt.model"
    SONIOX_STT_ENDPOINT = "soniox_stt.endpoint"
    SONIOX_STT_KEEPALIVE_S = "soniox_stt.keepalive_interval_s"
    SONIOX_STT_TRAILING_SILENCE_MS = "soniox_stt.trailing_silence_ms"
    OVERLAY_TARGET = "overlay.target"
    OVERLAY_SHOW_TRANSLATION = "overlay.show_translation"
    OVERLAY_SHOW_PEER_ORIGINAL = "overlay.show_peer_original"
    OVERLAY_CALIBRATION = "overlay.calibration"
    OVERLAY_DESKTOP_FLET = "overlay.desktop_flet"
    OSC_HOST = "osc.host"
    OSC_PORT = "osc.port"
    OSC_CHATBOX_ADDRESS = "osc.chatbox_address"
    OSC_CHATBOX_SEND = "osc.chatbox_send"
    OSC_CHATBOX_CLEAR = "osc.chatbox_clear"
    OSC_CHATBOX_MAX_CHARS = "osc.chatbox_max_chars"
    OSC_VRC_MIC_INTERCEPT = "osc.vrc_mic_intercept"
    OSC_CHATBOX_INCLUDE_SOURCE = "osc.chatbox_include_source"
    SECRETS_BACKEND = "secrets.backend"
    SECRETS_ENCRYPTED_FILE_PATH = "secrets.encrypted_file_path"
    UI_LOCALE = "ui.locale"
    CLIPBOARD_AUTO_TRANSLATE = "ui.clipboard_auto_translate_enabled"
    INTEGRATED_CONTEXT_ENABLED = "ui.integrated_context_enabled"
    SYSTEM_PROMPT = "system_prompt"


@dataclass(frozen=True, slots=True)
class StringMapValue:
    entries: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
            for entry in entries
        ):
            raise TypeError("string map entries must contain only string pairs")
        object.__setattr__(self, "entries", tuple((key, value) for key, value in entries))


@dataclass(frozen=True, slots=True)
class StringListMapValue:
    entries: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], tuple)
            or any(not isinstance(item, str) for item in entry[1])
            for entry in entries
        ):
            raise TypeError("string-list map entries must contain immutable strings")
        object.__setattr__(
            self,
            "entries",
            entries,
        )


@dataclass(frozen=True, slots=True)
class JsonScalarEntry:
    key: str
    value: str | int | float | bool | None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise TypeError("JSON scalar key must be text")
        if type(self.value) not in (str, int, float, bool, type(None)):
            raise TypeError("JSON scalar value must be immutable and scalar")


@dataclass(frozen=True, slots=True)
class LocalExtraBodyValue:
    entries: tuple[JsonScalarEntry, ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, JsonScalarEntry) for entry in entries):
            raise TypeError("local extra-body entries must be JsonScalarEntry values")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True, slots=True)
class TranslationFallbackValue:
    enabled: bool
    model: str
    connection: str
    selection_alias: str

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or any(
            not isinstance(value, str)
            for value in (self.model, self.connection, self.selection_alias)
        ):
            raise TypeError("fallback fields must be immutable typed scalars")
        from puripuly_heart.config.settings_vnext.schema import TranslationFallbackIntent

        canonical = TranslationFallbackIntent(selection_alias=self.selection_alias)
        if (self.enabled, self.model, self.connection) != (
            canonical.enabled,
            canonical.model,
            canonical.connection,
        ):
            raise ValueError("fallback fields are inconsistent with selection alias")


@dataclass(frozen=True, slots=True)
class OverlayCalibrationValue:
    anchor: str = "head_locked"
    offset_x: float = 0.0
    offset_y: float = -0.45
    distance: float = 1.1
    text_scale: float = 1.0
    background_alpha: float = 0.24

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, str):
            raise TypeError("calibration anchor must be text")
        numbers = (
            self.offset_x,
            self.offset_y,
            self.distance,
            self.text_scale,
            self.background_alpha,
        )
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in numbers):
            raise TypeError("calibration values must be finite numbers")
        if self.anchor != "head_locked" or self.distance <= 0 or self.text_scale <= 0:
            raise ValueError("unsupported calibration value")
        if not 0 <= self.background_alpha <= 1:
            raise ValueError("calibration alpha must be in 0..1")


@dataclass(frozen=True, slots=True)
class DesktopOverlayValue:
    size_preset: str = "medium"
    x: int | float | None = None
    y: int | float | None = None
    background_alpha: float = 0.6

    def __post_init__(self) -> None:
        if self.size_preset not in {"tiny", "xsmall", "small", "medium", "large", "xlarge"}:
            raise ValueError("unsupported desktop size preset")
        for value in (self.x, self.y):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise TypeError("desktop positions must be finite numbers or null")
        if type(self.background_alpha) not in (int, float) or not math.isfinite(
            self.background_alpha
        ):
            raise TypeError("desktop alpha must be a finite number")
        if not 0 <= self.background_alpha <= 1:
            raise ValueError("desktop alpha must be in 0..1")


SettingValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple[str, ...]
    | StringMapValue
    | StringListMapValue
    | LocalExtraBodyValue
    | TranslationFallbackValue
    | OverlayCalibrationValue
    | DesktopOverlayValue
)


def _is_deeply_immutable_setting_value(value: SettingValue) -> bool:
    if isinstance(value, tuple):
        return all(isinstance(item, str) for item in value)
    return isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
            type(None),
            StringMapValue,
            StringListMapValue,
            LocalExtraBodyValue,
            TranslationFallbackValue,
            OverlayCalibrationValue,
            DesktopOverlayValue,
        ),
    )


@dataclass(frozen=True, slots=True)
class SettingChange:
    field: SettingsField
    value: SettingValue

    def __post_init__(self) -> None:
        if not isinstance(self.field, SettingsField):
            raise TypeError("field must be SettingsField")
        if not _is_deeply_immutable_setting_value(self.value):
            raise TypeError("setting value must be deeply immutable")


@dataclass(frozen=True, slots=True)
class _SettingsCommand:
    changes: tuple[SettingChange, ...]
    expected_revision: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))
        if any(not isinstance(change, SettingChange) for change in self.changes):
            raise TypeError("changes must contain SettingChange values")

    def _require_fields(self, allowed: frozenset[SettingsField]) -> None:
        if any(change.field not in allowed for change in self.changes):
            raise ValueError("setting field is owned by another command surface")


_TRANSLATION_FIELDS = frozenset(
    field
    for field in SettingsField
    if field.value.startswith(
        ("translation.", "openrouter.", "qwen.", "cerebras.", "local_llm.", "llm.")
    )
)
_STT_FIELDS = frozenset(
    field
    for field in SettingsField
    if field.value.startswith(
        (
            "provider.",
            "languages.",
            "audio.",
            "desktop_audio.",
            "stt.",
            "deepgram_stt.",
            "qwen_asr_stt.",
            "soniox_stt.",
        )
    )
)
_OVERLAY_FIELDS = frozenset(
    field for field in SettingsField if field.value.startswith(("overlay.", "osc."))
)
_UI_FIELDS = frozenset(SettingsField) - _TRANSLATION_FIELDS - _STT_FIELDS - _OVERLAY_FIELDS


@dataclass(frozen=True, slots=True)
class TranslationProviderSettingsCommand(_SettingsCommand):
    def __post_init__(self) -> None:
        super(TranslationProviderSettingsCommand, self).__post_init__()
        self._require_fields(_TRANSLATION_FIELDS)


@dataclass(frozen=True, slots=True)
class SttLanguageAudioSettingsCommand(_SettingsCommand):
    def __post_init__(self) -> None:
        super(SttLanguageAudioSettingsCommand, self).__post_init__()
        self._require_fields(_STT_FIELDS)


@dataclass(frozen=True, slots=True)
class OverlayOscOutputSettingsCommand(_SettingsCommand):
    def __post_init__(self) -> None:
        super(OverlayOscOutputSettingsCommand, self).__post_init__()
        self._require_fields(_OVERLAY_FIELDS)


@dataclass(frozen=True, slots=True)
class UiPromptClipboardSettingsCommand(_SettingsCommand):
    def __post_init__(self) -> None:
        super(UiPromptClipboardSettingsCommand, self).__post_init__()
        self._require_fields(_UI_FIELDS)


SettingsCommand: TypeAlias = (
    TranslationProviderSettingsCommand
    | SttLanguageAudioSettingsCommand
    | OverlayOscOutputSettingsCommand
    | UiPromptClipboardSettingsCommand
)


@dataclass(frozen=True, slots=True)
class ApplicationSettingsSnapshot:
    leaves: tuple[tuple[tuple[str, ...], SettingValue], ...]
    revision: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(path, tuple)
            or any(not isinstance(part, str) for part in path)
            or not _is_deeply_immutable_setting_value(value)
            for path, value in self.leaves
        ):
            raise TypeError("snapshot leaves must be deeply immutable")
        if not isinstance(self.revision, str):
            raise TypeError("snapshot revision must be text")
        object.__setattr__(
            self,
            "leaves",
            tuple((tuple(path), value) for path, value in self.leaves),
        )


@dataclass(frozen=True, slots=True)
class SettingsCommandResult:
    status: str
    snapshot: ApplicationSettingsSnapshot
    message: UserMessageRef | None = None
    diagnostics: ErrorDiagnostics | None = None
    cancellation_count: int = 0
    committed_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str):
            raise TypeError("status must be text")
        if not isinstance(self.snapshot, ApplicationSettingsSnapshot):
            raise TypeError("snapshot must be ApplicationSettingsSnapshot")
        if self.message is not None and not isinstance(self.message, UserMessageRef):
            raise TypeError("message must be UserMessageRef or null")
        if self.diagnostics is not None and not isinstance(self.diagnostics, ErrorDiagnostics):
            raise TypeError("diagnostics must be ErrorDiagnostics or null")


class OperationalField(str, Enum):
    GITHUB_STAR_CLICKED = "github_star_prompt.clicked"
    GITHUB_STAR_LAST_SHOWN_AT = "github_star_prompt.last_shown_at"
    GITHUB_STAR_SHOW_COUNT = "github_star_prompt.show_count"
    GITHUB_STAR_TRANSLATION_SUCCESS_OBSERVED = "github_star_prompt.translation_success_observed"
    GITHUB_STAR_ELIGIBLE_LAUNCH_COUNT = "github_star_prompt.eligible_launch_count"
    PEER_TRANSLATION_EULA_ACCEPTED = "peer_translation.eula_accepted"
    INTEGRATED_CONTEXT_BOOTSTRAPPED = "integrated_context.bootstrapped"


@dataclass(frozen=True, slots=True)
class GithubStarClickedCommand:
    value: bool
    expected_revision: str


@dataclass(frozen=True, slots=True)
class GithubStarLastShownAtCommand:
    value: str | None
    expected_revision: str


@dataclass(frozen=True, slots=True)
class GithubStarShowCountCommand:
    value: int
    expected_revision: str


@dataclass(frozen=True, slots=True)
class GithubStarTranslationSuccessObservedCommand:
    value: bool
    expected_revision: str


@dataclass(frozen=True, slots=True)
class GithubStarEligibleLaunchCountCommand:
    value: int
    expected_revision: str


@dataclass(frozen=True, slots=True)
class PeerTranslationEulaAcceptedCommand:
    value: bool
    expected_revision: str


@dataclass(frozen=True, slots=True)
class IntegratedContextBootstrappedCommand:
    value: bool
    expected_revision: str


OperationalStateCommand: TypeAlias = (
    GithubStarClickedCommand
    | GithubStarLastShownAtCommand
    | GithubStarShowCountCommand
    | GithubStarTranslationSuccessObservedCommand
    | GithubStarEligibleLaunchCountCommand
    | PeerTranslationEulaAcceptedCommand
    | IntegratedContextBootstrappedCommand
)


@dataclass(frozen=True, slots=True)
class OperationalStateSnapshot:
    leaves: tuple[tuple[tuple[str, ...], str | int | bool | None], ...]
    revision: str

    def __post_init__(self) -> None:
        leaves = tuple(self.leaves)
        if any(
            not isinstance(entry, tuple)
            or len(entry) != 2
            or not isinstance(entry[0], tuple)
            or any(not isinstance(part, str) for part in entry[0])
            or type(entry[1]) not in (str, int, bool, type(None))
            for entry in leaves
        ):
            raise TypeError("operational snapshot leaves must be immutable scalar pairs")
        if not isinstance(self.revision, str):
            raise TypeError("operational revision must be text")
        object.__setattr__(
            self,
            "leaves",
            leaves,
        )


@dataclass(frozen=True, slots=True)
class OperationalCommandResult:
    status: str
    snapshot: OperationalStateSnapshot
    diagnostics: ErrorDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class SetSecretCommand:
    key: str
    value: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ClearSecretCommand:
    key: str


@dataclass(frozen=True, slots=True)
class SecretMetadataQuery:
    key: str


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    key: str
    present: bool
    revision: str | None
    verification: SecretVerificationStatus
    source: SecretSourceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.verification, SecretVerificationStatus):
            raise TypeError("verification must be SecretVerificationStatus")
        if not isinstance(self.source, SecretSourceStatus):
            raise TypeError("source must be SecretSourceStatus")


class SecretVerificationStatus(str, Enum):
    UNKNOWN = "unknown"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


class SecretSourceStatus(str, Enum):
    NONE = "none"
    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted_file"
    ENVIRONMENT = "environment"


class ApplicationSettingsCommandPort(Protocol):
    async def execute(self, command: SettingsCommand) -> SettingsCommandResult: ...


class ApplicationSettingsQueryPort(Protocol):
    async def snapshot(self) -> ApplicationSettingsSnapshot: ...


class OperationalStateCommandPort(Protocol):
    async def execute_operational(
        self, command: OperationalStateCommand
    ) -> OperationalCommandResult: ...


class OperationalStateQueryPort(Protocol):
    async def operational_snapshot(self) -> OperationalStateSnapshot: ...


class SecretCommandPort(Protocol):
    async def set_secret(self, command: SetSecretCommand) -> SecretMetadata: ...

    async def clear_secret(self, command: ClearSecretCommand) -> SecretMetadata: ...


class SecretQueryPort(Protocol):
    async def secret_metadata(self, query: SecretMetadataQuery) -> SecretMetadata: ...


__all__ = [name for name in globals() if not name.startswith("_")]
