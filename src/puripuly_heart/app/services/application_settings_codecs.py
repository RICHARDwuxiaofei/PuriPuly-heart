from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final

from puripuly_heart.app.ports.application_settings import (
    CaptureTargetValue,
    DesktopOverlayValue,
    GithubStarClickedCommand,
    GithubStarEligibleLaunchCountCommand,
    GithubStarLastShownAtCommand,
    GithubStarShowCountCommand,
    GithubStarTranslationSuccessObservedCommand,
    IntegratedContextBootstrappedCommand,
    LocalExtraBodyValue,
    OperationalField,
    OperationalStateCommand,
    OverlayCalibrationValue,
    PeerTranslationEulaAcceptedCommand,
    SettingsField,
    SettingsSurface,
    SettingValue,
    StringListMapValue,
    StringMapValue,
    TranslationFallbackValue,
)
from puripuly_heart.config.llm_profiles import PROFILE_BY_ALIAS
from puripuly_heart.config.runtime_resolution import (
    DEEPSEEK_MODEL_V4_FLASH,
    DEEPSEEK_MODEL_V4_PRO,
    GEMINI_MODEL_3_FLASH,
    GEMINI_MODEL_31_FLASH_LITE,
    LLM_PROVIDERS,
    QWEN_MODEL_35_FLASH,
    QWEN_MODEL_35_PLUS,
    STT_PROVIDERS,
    TRANSLATION_CONNECTIONS,
    derive_translation_runtime_intent_from_compatibility,
)

CanonicalPath = tuple[str, ...]
CanonicalLeaf = tuple[CanonicalPath, SettingValue]
OPENROUTER_SELECTION_ALIASES: Final = frozenset(PROFILE_BY_ALIAS)


class CodecKind(str, Enum):
    TEXT = "text"
    EMPTY_TEXT = "empty_text"
    OPTIONAL_TEXT = "optional_text"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING_LIST = "string_list"
    STRING_MAP = "string_map"
    STRING_LIST_MAP = "string_list_map"
    LOCAL_EXTRA_BODY = "local_extra_body"
    FALLBACK = "fallback"
    CALIBRATION = "calibration"
    DESKTOP_OVERLAY = "desktop_overlay"
    CAPTURE_TARGET = "capture_target"


@dataclass(frozen=True, slots=True)
class FieldCodec:
    field: SettingsField
    owner: SettingsSurface
    canonical_paths: tuple[CanonicalPath, ...]
    kind: CodecKind
    minimum: float | None = None
    maximum: float | None = None
    choices: frozenset[str] = frozenset()

    def encode(self, value: SettingValue) -> tuple[CanonicalLeaf, ...]:
        normalized = self.normalize(value)
        if self.kind == CodecKind.FALLBACK:
            return tuple(zip(self.canonical_paths, _fallback_leaves(normalized), strict=True))
        if self.kind == CodecKind.CALIBRATION:
            return tuple(zip(self.canonical_paths, _calibration_leaves(normalized), strict=True))
        if self.kind == CodecKind.DESKTOP_OVERLAY:
            return tuple(zip(self.canonical_paths, _desktop_leaves(normalized), strict=True))
        if self.kind == CodecKind.CAPTURE_TARGET:
            from puripuly_heart.config.settings_vnext.schema import (
                CaptureTargetIntent,
                ProcessCaptureTargetIntent,
            )

            assert isinstance(normalized, CaptureTargetValue)
            if normalized.kind == "default_output_device":
                target = CaptureTargetIntent.default_output_device()
            elif normalized.kind == "named_output_device":
                target = CaptureTargetIntent.named_output_device(normalized.device_name or "")
            elif normalized.process_kind == "discord":
                target = CaptureTargetIntent.process_target(
                    ProcessCaptureTargetIntent.discord(normalized.discord_channel or "")
                )
            elif normalized.process_kind == "vrchat":
                target = CaptureTargetIntent.process_target(
                    ProcessCaptureTargetIntent.vrchat(normalized.executable_identity or "")
                )
            else:
                target = CaptureTargetIntent.process_target(
                    ProcessCaptureTargetIntent.generic_executable(
                        normalized.executable_identity or ""
                    )
                )
            return ((self.canonical_paths[0], target),)  # type: ignore[return-value]
        return ((self.canonical_paths[0], normalized),)

    def decode(self, leaves: tuple[SettingValue, ...]) -> SettingValue:
        if len(leaves) != len(self.canonical_paths):
            raise ValueError("canonical leaf count does not match codec")
        if self.kind == CodecKind.FALLBACK:
            return TranslationFallbackValue(leaves[0], leaves[1], leaves[2], leaves[3])
        if self.kind == CodecKind.CALIBRATION:
            return OverlayCalibrationValue(*leaves)
        if self.kind == CodecKind.DESKTOP_OVERLAY:
            return DesktopOverlayValue(str(leaves[0]), leaves[1], leaves[2], float(leaves[3]))
        if self.kind == CodecKind.CAPTURE_TARGET:
            target = leaves[0]
            process = target.process  # type: ignore[union-attr]
            return CaptureTargetValue(
                kind=target.kind,  # type: ignore[union-attr]
                device_name=target.device_name,  # type: ignore[union-attr]
                process_kind=None if process is None else process.kind,
                executable_identity=None if process is None else process.executable_identity,
                discord_channel=None if process is None else process.discord_channel,
            )
        return self.normalize(leaves[0])

    def normalize(self, value: SettingValue) -> SettingValue:
        if self.kind == CodecKind.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("expected non-empty text")
            normalized: SettingValue = value.strip()
        elif self.kind == CodecKind.EMPTY_TEXT:
            if not isinstance(value, str):
                raise TypeError("expected text")
            normalized = value.strip()
        elif self.kind == CodecKind.OPTIONAL_TEXT:
            if value is not None and not isinstance(value, str):
                raise TypeError("expected text or null")
            normalized = value.strip() if isinstance(value, str) and value.strip() else None
        elif self.kind == CodecKind.BOOLEAN:
            if type(value) is not bool:
                raise TypeError("expected boolean")
            normalized = value
        elif self.kind == CodecKind.INTEGER:
            if type(value) is not int:
                raise TypeError("expected integer")
            normalized = value
        elif self.kind == CodecKind.NUMBER:
            if type(value) not in (int, float):
                raise TypeError("expected number")
            normalized = float(value)
        elif self.kind == CodecKind.STRING_LIST:
            if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
                raise TypeError("expected tuple of strings")
            normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        elif self.kind == CodecKind.STRING_MAP:
            if not isinstance(value, StringMapValue):
                raise TypeError("expected StringMapValue")
            normalized = StringMapValue(_unique_pairs(value.entries))
            if self.field == SettingsField.TRANSLATION_CONNECTION_HISTORY and len(
                _normalized_connection_history(normalized)
            ) != len(normalized.entries):
                raise ValueError("invalid translation connection history")
        elif self.kind == CodecKind.STRING_LIST_MAP:
            if not isinstance(value, StringListMapValue):
                raise TypeError("expected StringListMapValue")
            if len({key for key, _ in value.entries}) != len(value.entries):
                raise ValueError("duplicate map key")
            if any(
                not key.strip()
                or not isinstance(items, tuple)
                or any(not isinstance(item, str) or not item.strip() for item in items)
                for key, items in value.entries
            ):
                raise ValueError("invalid string-list map")
            normalized = StringListMapValue(
                tuple(
                    (key.strip(), tuple(dict.fromkeys(item.strip() for item in items)))
                    for key, items in value.entries
                )
            )
        elif self.kind == CodecKind.LOCAL_EXTRA_BODY:
            if not isinstance(value, LocalExtraBodyValue):
                raise TypeError("expected LocalExtraBodyValue")
            if len({entry.key for entry in value.entries}) != len(value.entries):
                raise ValueError("duplicate local extra-body key")
            from puripuly_heart.config.settings_vnext.schema import LocalLLMIntent

            LocalLLMIntent(extra_body={entry.key: entry.value for entry in value.entries})
            normalized = value
        elif self.kind == CodecKind.CAPTURE_TARGET:
            if not isinstance(value, CaptureTargetValue):
                raise TypeError("expected CaptureTargetValue")
            normalized = value
        else:
            normalized = _validate_composite(self.kind, value)
        if isinstance(normalized, str) and self.choices and normalized not in self.choices:
            raise ValueError("unsupported enum value")
        if type(normalized) in (int, float):
            number = float(normalized)
            if not math.isfinite(number):
                raise ValueError("value must be finite")
            if self.minimum is not None and number < self.minimum:
                raise ValueError("value below minimum")
            if self.maximum is not None and number > self.maximum:
                raise ValueError("value above maximum")
            if (
                self.field
                in {
                    SettingsField.STT_DRAIN_TIMEOUT_S,
                    SettingsField.SONIOX_STT_KEEPALIVE_S,
                }
                and number <= 0
            ):
                raise ValueError("value must be strictly positive")
        return normalized


def _unique_pairs(entries: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    if any(not key.strip() or not value.strip() for key, value in entries):
        raise ValueError("map keys and values must be non-empty")
    if len({key for key, _ in entries}) != len(entries):
        raise ValueError("duplicate map key")
    return tuple((key.strip(), value.strip()) for key, value in entries)


_HISTORY_CONNECTIONS = {
    "gemma4": {"managed", "openrouter"},
    "deepseek_v4_flash": {"managed", "managed_china", "openrouter", "official_byok"},
    "deepseek_v4_pro": {"official_byok"},
    "gemini3_flash": {"official_byok", "openrouter"},
    "gemini31_flash_lite": {"official_byok", "openrouter"},
    "qwen35_plus": {"official_byok"},
    "local_llm": {"ollama"},
    "gemma4_31b_cerebras": {"official_byok"},
}


def _normalized_connection_history(value: StringMapValue) -> dict[str, str]:
    return {
        model: connection
        for model, connection in _unique_pairs(value.entries)
        if model in _HISTORY_CONNECTIONS and connection in _HISTORY_CONNECTIONS[model]
    }


def _validate_composite(kind: CodecKind, value: SettingValue) -> SettingValue:
    expected = {
        CodecKind.FALLBACK: TranslationFallbackValue,
        CodecKind.CALIBRATION: OverlayCalibrationValue,
        CodecKind.DESKTOP_OVERLAY: DesktopOverlayValue,
    }[kind]
    if not isinstance(value, expected):
        raise TypeError(f"expected {expected.__name__}")
    return value


def _fallback_leaves(value: SettingValue) -> tuple[SettingValue, ...]:
    value = _validate_composite(CodecKind.FALLBACK, value)
    from puripuly_heart.config.settings_vnext.schema import TranslationFallbackIntent

    canonical = TranslationFallbackIntent(selection_alias=value.selection_alias)
    if (value.enabled, value.model, value.connection) != (
        canonical.enabled,
        canonical.model,
        canonical.connection,
    ):
        raise ValueError("fallback fields are inconsistent with selection alias")
    return (value.enabled, value.model, value.connection, value.selection_alias)


def _calibration_leaves(value: SettingValue) -> tuple[SettingValue, ...]:
    value = _validate_composite(CodecKind.CALIBRATION, value)
    if value.anchor != "head_locked":
        raise ValueError("unsupported calibration anchor")
    numbers = (
        value.offset_x,
        value.offset_y,
        value.distance,
        value.text_scale,
        value.background_alpha,
    )
    if any(
        type(number) not in (int, float) or not math.isfinite(float(number)) for number in numbers
    ):
        raise ValueError("calibration values must be finite numbers")
    if value.distance <= 0 or value.text_scale <= 0 or not 0 <= value.background_alpha <= 1:
        raise ValueError("calibration value outside canonical range")
    return (value.anchor, *numbers)


def _desktop_leaves(value: SettingValue) -> tuple[SettingValue, ...]:
    value = _validate_composite(CodecKind.DESKTOP_OVERLAY, value)
    if value.size_preset not in {"tiny", "xsmall", "small", "medium", "large", "xlarge"}:
        raise ValueError("unsupported desktop size")
    for position in (value.x, value.y):
        if position is not None and (
            type(position) not in (int, float) or not math.isfinite(float(position))
        ):
            raise ValueError("desktop position must be finite or null")
    if (
        type(value.background_alpha) not in (int, float)
        or not math.isfinite(float(value.background_alpha))
        or not 0 <= value.background_alpha <= 1
    ):
        raise ValueError("desktop alpha outside canonical range")
    return (value.size_preset, value.x, value.y, float(value.background_alpha))


def _codec(
    field: SettingsField,
    owner: SettingsSurface,
    path: CanonicalPath,
    kind: CodecKind = CodecKind.TEXT,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: tuple[str, ...] = (),
) -> FieldCodec:
    return FieldCodec(field, owner, (path,), kind, minimum, maximum, frozenset(choices))


T = SettingsSurface.TRANSLATION_PROVIDER
S = SettingsSurface.STT_LANGUAGE_AUDIO
V = SettingsSurface.OVERLAY_OSC_OUTPUT
U = SettingsSurface.UI_PROMPT_CLIPBOARD

_CODECS = [
    _codec(
        SettingsField.TRANSLATION_MODEL,
        T,
        ("translation", "model"),
        choices=(
            "gemma4",
            "deepseek_v4_flash",
            "deepseek_v4_pro",
            "gemini3_flash",
            "gemini31_flash_lite",
            "qwen35_plus",
            "local_llm",
            "gemma4_31b_cerebras",
        ),
    ),
    _codec(
        SettingsField.TRANSLATION_CONNECTION,
        T,
        ("translation", "connection"),
        choices=TRANSLATION_CONNECTIONS,
    ),
    _codec(
        SettingsField.TRANSLATION_CONNECTION_HISTORY,
        T,
        ("translation", "connection_history"),
        CodecKind.STRING_MAP,
    ),
    FieldCodec(
        SettingsField.TRANSLATION_FALLBACK,
        T,
        (
            ("translation", "fallback", "enabled"),
            ("translation", "fallback", "model"),
            ("translation", "fallback", "connection"),
            ("translation", "fallback", "selection_alias"),
        ),
        CodecKind.FALLBACK,
    ),
    _codec(
        SettingsField.OPENROUTER_LLM_MODEL,
        T,
        ("translation", "openrouter_model"),
        choices=(
            "google/gemma-4-26b-a4b-it",
            "qwen/qwen3.5-flash-02-23",
            "deepseek/deepseek-v4-flash",
            "google/gemini-3-flash-preview",
            "google/gemini-3.1-flash-lite",
        ),
    ),
    _codec(
        SettingsField.OPENROUTER_ROUTING_MODE,
        T,
        ("translation", "openrouter_routing_mode"),
        choices=("latency",),
    ),
    _codec(
        SettingsField.OPENROUTER_PROVIDER_ROUTING,
        T,
        ("translation", "openrouter_provider_routing"),
        choices=("default", "deepseek_only", "google_gemini_latency"),
    ),
    _codec(
        SettingsField.OPENROUTER_SELECTED_SOURCE,
        T,
        ("translation", "openrouter_selected_source"),
        choices=("none", "managed", "byok"),
    ),
    _codec(
        SettingsField.OPENROUTER_SELECTION_ALIAS,
        T,
        ("translation", "openrouter_selection_alias"),
        CodecKind.OPTIONAL_TEXT,
        choices=tuple(OPENROUTER_SELECTION_ALIASES),
    ),
    _codec(
        SettingsField.OPENROUTER_BROKER_BASE_URL, T, ("translation", "openrouter_broker_base_url")
    ),
    _codec(
        SettingsField.QWEN_LLM_MODEL,
        T,
        ("translation", "qwen", "llm_model"),
        choices=(QWEN_MODEL_35_FLASH, QWEN_MODEL_35_PLUS),
    ),
    _codec(
        SettingsField.QWEN_REGION,
        T,
        ("translation", "qwen", "region"),
        choices=("beijing", "singapore"),
    ),
    _codec(
        SettingsField.CEREBRAS_LLM_MODEL,
        T,
        ("translation", "cerebras", "llm_model"),
        choices=("gemma-4-31b",),
    ),
    _codec(
        SettingsField.LOCAL_LLM_BACKEND,
        T,
        ("local_llm", "backend"),
        choices=("ollama",),
    ),
    _codec(SettingsField.LOCAL_LLM_BASE_URL, T, ("local_llm", "base_url")),
    _codec(SettingsField.LOCAL_LLM_MODEL, T, ("local_llm", "model")),
    _codec(
        SettingsField.LOCAL_LLM_EXTRA_BODY,
        T,
        ("local_llm", "extra_body"),
        CodecKind.LOCAL_EXTRA_BODY,
    ),
    _codec(
        SettingsField.LLM_CONCURRENCY_LIMIT,
        T,
        ("translation", "concurrency_limit"),
        CodecKind.INTEGER,
        minimum=1,
    ),
]
_TEXT_PATHS = {
    SettingsField.PROVIDER_STT: (S, ("stt", "provider")),
    SettingsField.PROVIDER_PEER_STT: (S, ("peer_stt", "provider")),
    SettingsField.SOURCE_LANGUAGE: (S, ("languages", "source_language")),
    SettingsField.TARGET_LANGUAGE: (S, ("languages", "target_language")),
    SettingsField.PEER_SOURCE_LANGUAGE: (S, ("languages", "peer_source_language")),
    SettingsField.PEER_TARGET_LANGUAGE: (S, ("languages", "peer_target_language")),
    SettingsField.PEER_SOURCE_MODE: (S, ("languages", "peer_source_mode")),
    SettingsField.AUDIO_INPUT_HOST_API: (S, ("audio", "input_host_api")),
    SettingsField.AUDIO_INPUT_DEVICE: (S, ("audio", "input_device")),
    SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE: (S, ("desktop_audio", "output_device")),
    SettingsField.DEEPGRAM_STT_MODEL: (S, ("stt", "deepgram", "model")),
    SettingsField.QWEN_ASR_STT_MODEL: (S, ("stt", "qwen_asr", "model")),
    SettingsField.SONIOX_STT_MODEL: (S, ("stt", "soniox", "model")),
    SettingsField.SONIOX_STT_ENDPOINT: (S, ("stt", "soniox", "endpoint")),
    SettingsField.OVERLAY_TARGET: (V, ("overlay", "target")),
    SettingsField.OSC_HOST: (V, ("osc", "host")),
    SettingsField.OSC_CHATBOX_ADDRESS: (V, ("osc", "chatbox_address")),
    SettingsField.SECRETS_BACKEND: (U, ("secrets", "backend")),
    SettingsField.SECRETS_ENCRYPTED_FILE_PATH: (U, ("secrets", "encrypted_file_path")),
    SettingsField.UI_LOCALE: (U, ("ui", "locale")),
    SettingsField.SYSTEM_PROMPT: (U, ("prompts", "system_prompt")),
}
_CODECS.extend(_codec(field, owner, path) for field, (owner, path) in _TEXT_PATHS.items())
_CODECS.append(
    _codec(
        SettingsField.DESKTOP_AUDIO_CAPTURE_TARGET,
        S,
        ("desktop_audio", "capture_target"),
        CodecKind.CAPTURE_TARGET,
    )
)
_EMPTY_TEXT_PATHS = {
    SettingsField.AUDIO_INPUT_DEVICE: (S, ("audio", "input_device")),
    SettingsField.DESKTOP_AUDIO_OUTPUT_DEVICE: (S, ("desktop_audio", "output_device")),
    SettingsField.SYSTEM_PROMPT: (U, ("prompts", "system_prompt")),
}
_CODECS = [codec for codec in _CODECS if codec.field not in _EMPTY_TEXT_PATHS]
_CODECS.extend(
    _codec(field, owner, path, CodecKind.EMPTY_TEXT)
    for field, (owner, path) in _EMPTY_TEXT_PATHS.items()
)
_CODECS = [
    (
        _codec(
            codec.field,
            codec.owner,
            codec.canonical_paths[0],
            codec.kind,
            minimum=codec.minimum,
            maximum=codec.maximum,
            choices=STT_PROVIDERS,
        )
        if codec.field in {SettingsField.PROVIDER_STT, SettingsField.PROVIDER_PEER_STT}
        else codec
    )
    for codec in _CODECS
]
_ENUM_DOMAINS = {
    SettingsField.PEER_SOURCE_MODE: ("manual", "soniox_auto"),
    SettingsField.OVERLAY_TARGET: ("steamvr", "desktop"),
    SettingsField.SECRETS_BACKEND: ("keyring", "encrypted_file"),
}
_CODECS = [
    (
        _codec(
            codec.field,
            codec.owner,
            codec.canonical_paths[0],
            codec.kind,
            minimum=codec.minimum,
            maximum=codec.maximum,
            choices=_ENUM_DOMAINS[codec.field],
        )
        if codec.field in _ENUM_DOMAINS
        else codec
    )
    for codec in _CODECS
]

_LIST_PATHS = {
    SettingsField.PEER_EXPECTED_LANGUAGES: ("languages", "peer_expected_languages"),
    SettingsField.RECENT_SOURCE_LANGUAGES: ("languages", "recent_source_languages"),
    SettingsField.RECENT_TARGET_LANGUAGES: ("languages", "recent_target_languages"),
}
_CODECS.extend(_codec(field, S, path, CodecKind.STRING_LIST) for field, path in _LIST_PATHS.items())
_CODECS.append(
    _codec(SettingsField.STT_CUSTOM_TERMS, S, ("stt", "custom_terms"), CodecKind.STRING_LIST_MAP)
)

_BOOL_PATHS = {
    SettingsField.STT_LOW_LATENCY_MODE: (S, ("stt", "low_latency_mode")),
    SettingsField.STT_CUSTOM_VOCABULARY_ENABLED: (S, ("stt", "custom_vocabulary_enabled")),
    SettingsField.OVERLAY_SHOW_TRANSLATION: (V, ("overlay", "show_translation")),
    SettingsField.OVERLAY_SHOW_PEER_ORIGINAL: (V, ("overlay", "show_peer_original")),
    SettingsField.OSC_CHATBOX_SEND: (V, ("osc", "chatbox_send")),
    SettingsField.OSC_CHATBOX_CLEAR: (V, ("osc", "chatbox_clear")),
    SettingsField.OSC_VRC_MIC_INTERCEPT: (V, ("osc", "vrc_mic_intercept")),
    SettingsField.OSC_CHATBOX_INCLUDE_SOURCE: (V, ("osc", "chatbox_include_source")),
    SettingsField.CLIPBOARD_AUTO_TRANSLATE: (U, ("clipboard", "auto_translate_enabled")),
    SettingsField.INTEGRATED_CONTEXT_ENABLED: (U, ("integrated_context", "enabled")),
}
_CODECS.extend(
    _codec(field, owner, path, CodecKind.BOOLEAN) for field, (owner, path) in _BOOL_PATHS.items()
)

_INT_PATHS = {
    SettingsField.AUDIO_RING_BUFFER_MS: (("audio", "ring_buffer_ms"), 1, None),
    SettingsField.DESKTOP_VAD_HANGOVER_MS: (("desktop_audio", "vad_hangover_ms"), 0, None),
    SettingsField.DESKTOP_VAD_PRE_ROLL_MS: (("desktop_audio", "vad_pre_roll_ms"), 0, None),
    SettingsField.STT_LOW_LATENCY_VAD_HANGOVER_MS: (
        ("stt", "low_latency_vad_hangover_ms"),
        0,
        None,
    ),
    SettingsField.STT_LOW_LATENCY_MERGE_GAP_MS: (("stt", "low_latency_merge_gap_ms"), 0, None),
    SettingsField.STT_LOW_LATENCY_SPEC_RETRY_MAX: (("stt", "low_latency_spec_retry_max"), 0, None),
    SettingsField.SONIOX_STT_TRAILING_SILENCE_MS: (
        ("stt", "soniox", "trailing_silence_ms"),
        0,
        None,
    ),
    SettingsField.OSC_PORT: (("osc", "port"), 1, 65535),
    SettingsField.OSC_CHATBOX_MAX_CHARS: (("osc", "chatbox_max_chars"), 1, None),
}
_CODECS.extend(
    _codec(
        field,
        S if field.value.startswith(("audio", "desktop", "stt", "soniox")) else V,
        path,
        CodecKind.INTEGER,
        minimum=minimum,
        maximum=maximum,
    )
    for field, (path, minimum, maximum) in _INT_PATHS.items()
)

_NUMBER_PATHS = {
    SettingsField.DESKTOP_VAD_THRESHOLD: (("desktop_audio", "vad_speech_threshold"), 1),
    SettingsField.STT_DRAIN_TIMEOUT_S: (("stt", "drain_timeout_s"), None),
    SettingsField.STT_VAD_THRESHOLD: (("stt", "vad_speech_threshold"), 1),
    SettingsField.SONIOX_STT_KEEPALIVE_S: (("stt", "soniox", "keepalive_interval_s"), None),
}
_CODECS.extend(
    _codec(field, S, path, CodecKind.NUMBER, minimum=0, maximum=maximum)
    for field, (path, maximum) in _NUMBER_PATHS.items()
)

from puripuly_heart.config.overlay_calibration import OverlayCalibration

_CALIBRATION_PATHS = tuple(
    ("overlay", "calibration", name) for name in OverlayCalibration.__dataclass_fields__
)
_CODECS.extend(
    (
        FieldCodec(SettingsField.OVERLAY_CALIBRATION, V, _CALIBRATION_PATHS, CodecKind.CALIBRATION),
        FieldCodec(
            SettingsField.OVERLAY_DESKTOP_FLET,
            V,
            (
                ("overlay", "desktop_flet", "size_preset"),
                ("overlay", "desktop_flet", "position", "x"),
                ("overlay", "desktop_flet", "position", "y"),
                ("overlay", "desktop_flet", "visual", "background_alpha"),
            ),
            CodecKind.DESKTOP_OVERLAY,
        ),
    )
)

FIELD_CODECS: Final = MappingProxyType({codec.field: codec for codec in _CODECS})


@dataclass(frozen=True, slots=True)
class ProviderDiscriminatorState:
    gemini_model: str = "gemini-3.1-flash-lite"
    deepseek_model: str = "deepseek-v4-flash"
    qwen_model: str = "qwen3.5-plus"
    cerebras_model: str = "gemma-4-31b"
    local_backend: str = "ollama"
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = "llama3.1:8b"
    openrouter_model: str = "google/gemma-4-26b-a4b-it"
    openrouter_selected_source: str = "managed"
    openrouter_selection_alias: str | None = "gemma4_managed"
    openrouter_routing_mode: str = "latency"
    openrouter_provider_routing: str = "default"

    def __post_init__(self) -> None:
        if any(
            value is not None and not isinstance(value, str)
            for value in (
                self.gemini_model,
                self.deepseek_model,
                self.qwen_model,
                self.cerebras_model,
                self.local_backend,
                self.local_base_url,
                self.local_model,
                self.openrouter_model,
                self.openrouter_selected_source,
                self.openrouter_selection_alias,
                self.openrouter_routing_mode,
                self.openrouter_provider_routing,
            )
        ):
            raise TypeError("provider discriminators must be immutable text values")
        allowed = {
            "gemini_model": {GEMINI_MODEL_3_FLASH, GEMINI_MODEL_31_FLASH_LITE},
            "deepseek_model": {DEEPSEEK_MODEL_V4_FLASH, DEEPSEEK_MODEL_V4_PRO},
            "qwen_model": {QWEN_MODEL_35_FLASH, QWEN_MODEL_35_PLUS},
            "cerebras_model": {"gemma-4-31b"},
            "local_backend": {"ollama"},
            "openrouter_model": {
                "google/gemma-4-26b-a4b-it",
                "qwen/qwen3.5-flash-02-23",
                "deepseek/deepseek-v4-flash",
                "google/gemini-3-flash-preview",
                "google/gemini-3.1-flash-lite",
            },
            "openrouter_selected_source": {"none", "managed", "byok"},
            "openrouter_routing_mode": {"latency"},
            "openrouter_provider_routing": {
                "default",
                "deepseek_only",
                "google_gemini_latency",
            },
        }
        for name, values in allowed.items():
            if getattr(self, name) not in values:
                raise ValueError(f"invalid retained provider discriminator: {name}")
        if not self.local_base_url.strip() or not self.local_model.strip():
            raise ValueError("retained local provider fields must be non-empty")
        if self.openrouter_selected_source == "none":
            if self.openrouter_selection_alias is not None:
                raise ValueError("inactive OpenRouter state cannot retain an alias")
        else:
            if self.openrouter_selection_alias is None:
                raise ValueError("active OpenRouter state requires an alias")
            profile = PROFILE_BY_ALIAS.get(self.openrouter_selection_alias)
            if (
                profile is None
                or profile.openrouter_model != self.openrouter_model
                or profile.openrouter_source != self.openrouter_selected_source
            ):
                raise ValueError("retained OpenRouter profile is inconsistent")


@dataclass(frozen=True, slots=True)
class ProviderSelectionCommand:
    provider: str
    model: str
    connection_source: str
    routing_mode: str = "latency"
    provider_routing: str = "default"
    selection_alias: str | None = None
    connection_history: StringMapValue = StringMapValue(())
    retained: ProviderDiscriminatorState = ProviderDiscriminatorState()

    def __post_init__(self) -> None:
        if not isinstance(self.connection_history, StringMapValue):
            raise TypeError("connection history must be StringMapValue")
        if not isinstance(self.retained, ProviderDiscriminatorState):
            raise TypeError("retained state must be ProviderDiscriminatorState")


@dataclass(frozen=True, slots=True)
class CanonicalProviderSelection:
    provider: str
    model: str
    connection: str
    openrouter_model: str
    openrouter_selected_source: str
    openrouter_selection_alias: str | None
    openrouter_routing_mode: str
    openrouter_provider_routing: str
    gemini_model: str
    deepseek_model: str
    qwen_model: str
    cerebras_model: str
    local_backend: str
    local_base_url: str
    local_model: str
    connection_history: StringMapValue


def convert_provider_selection(command: ProviderSelectionCommand) -> CanonicalProviderSelection:
    provider = command.provider.strip()
    model = command.model.strip()
    source = command.connection_source.strip()
    if provider not in LLM_PROVIDERS or not model or source not in {"managed", "byok", "none"}:
        raise ValueError("provider selection is incomplete")
    from puripuly_heart.config.llm_profiles import (
        OPENROUTER_MODEL_DEEPSEEK_V4_FLASH,
        OPENROUTER_MODEL_GEMINI_3_FLASH,
        OPENROUTER_MODEL_GEMINI_31_FLASH_LITE,
        OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT,
        OPENROUTER_MODEL_QWEN_35_FLASH_02_23,
    )

    allowed_models = {
        "gemini": {GEMINI_MODEL_3_FLASH, GEMINI_MODEL_31_FLASH_LITE},
        "deepseek": {DEEPSEEK_MODEL_V4_FLASH, DEEPSEEK_MODEL_V4_PRO},
        "qwen": {QWEN_MODEL_35_FLASH, QWEN_MODEL_35_PLUS},
        "cerebras": {"gemma-4-31b"},
        "openrouter": {
            OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT,
            OPENROUTER_MODEL_DEEPSEEK_V4_FLASH,
            OPENROUTER_MODEL_QWEN_35_FLASH_02_23,
            OPENROUTER_MODEL_GEMINI_3_FLASH,
            OPENROUTER_MODEL_GEMINI_31_FLASH_LITE,
        },
    }
    if provider in allowed_models and model not in allowed_models[provider]:
        raise ValueError("provider and model are inconsistent")
    expected_sources = {"openrouter": {"managed", "byok"}, "local_llm": {"none"}}
    if source not in expected_sources.get(provider, {"byok"}):
        raise ValueError("provider and connection source are inconsistent")
    if command.selection_alias is not None and provider != "openrouter":
        raise ValueError("selection alias requires OpenRouter")
    if command.routing_mode != "latency":
        raise ValueError("unsupported OpenRouter routing mode")
    if command.provider_routing not in {
        "default",
        "deepseek_only",
        "google_gemini_latency",
    }:
        raise ValueError("unsupported OpenRouter provider routing")
    if provider != "openrouter" and command.provider_routing != "default":
        raise ValueError("provider routing requires OpenRouter")
    if command.provider_routing == "deepseek_only" and "deepseek" not in model:
        raise ValueError("deepseek-only routing requires a DeepSeek profile")
    if command.provider_routing == "google_gemini_latency" and "gemini" not in model:
        raise ValueError("Gemini routing requires a Gemini profile")
    derived = derive_translation_runtime_intent_from_compatibility(
        provider_llm=provider,
        openrouter_model=model if provider == "openrouter" else None,
        openrouter_selected_source=source,
        openrouter_provider_routing=command.provider_routing,
        gemini_model=model if provider == "gemini" else None,
        qwen_model=model if provider == "qwen" else None,
        deepseek_model=model if provider == "deepseek" else None,
        cerebras_model=model if provider == "cerebras" else None,
        connection_history=_normalized_connection_history(command.connection_history),
    )
    from puripuly_heart.config.llm_profiles import openrouter_alias_for_fields

    canonical_alias = (
        openrouter_alias_for_fields(model=model, source=source)
        if provider == "openrouter"
        else None
    )
    if provider == "openrouter" and canonical_alias is None:
        raise ValueError("unsupported OpenRouter model/source profile")
    if command.selection_alias is not None:
        profile = PROFILE_BY_ALIAS.get(command.selection_alias)
        if (
            profile is None
            or profile.openrouter_model != model
            or profile.openrouter_source != source
        ):
            raise ValueError("provider selection alias is inconsistent")
    history = _normalized_connection_history(command.connection_history)
    history[derived.model] = derived.connection
    retained = command.retained
    openrouter_model = retained.openrouter_model
    openrouter_source = retained.openrouter_selected_source
    openrouter_alias = retained.openrouter_selection_alias
    openrouter_routing_mode = retained.openrouter_routing_mode
    openrouter_provider_routing = retained.openrouter_provider_routing
    if provider == "openrouter":
        openrouter_model = model
        openrouter_source = source
        openrouter_alias = canonical_alias
        openrouter_routing_mode = command.routing_mode
        openrouter_provider_routing = command.provider_routing
    return CanonicalProviderSelection(
        provider,
        derived.model,
        derived.connection,
        openrouter_model,
        openrouter_source,
        openrouter_alias,
        openrouter_routing_mode,
        openrouter_provider_routing,
        model if provider == "gemini" else retained.gemini_model,
        model if provider == "deepseek" else retained.deepseek_model,
        model if provider == "qwen" else retained.qwen_model,
        model if provider == "cerebras" else retained.cerebras_model,
        retained.local_backend,
        retained.local_base_url,
        model if provider == "local_llm" else retained.local_model,
        StringMapValue(tuple(sorted(history.items()))),
    )


def project_provider_selection(
    selection: CanonicalProviderSelection,
) -> ProviderSelectionCommand:
    provider_models = {
        "gemini": selection.gemini_model,
        "deepseek": selection.deepseek_model,
        "qwen": selection.qwen_model,
        "cerebras": selection.cerebras_model,
        "local_llm": selection.local_model,
        "openrouter": selection.openrouter_model,
    }
    source = (
        selection.openrouter_selected_source
        if selection.provider == "openrouter"
        else "none" if selection.provider == "local_llm" else "byok"
    )
    retained = ProviderDiscriminatorState(
        selection.gemini_model,
        selection.deepseek_model,
        selection.qwen_model,
        selection.cerebras_model,
        selection.local_backend,
        selection.local_base_url,
        selection.local_model,
        selection.openrouter_model,
        selection.openrouter_selected_source,
        selection.openrouter_selection_alias,
        selection.openrouter_routing_mode,
        selection.openrouter_provider_routing,
    )
    return ProviderSelectionCommand(
        selection.provider,
        provider_models[selection.provider],
        source,
        selection.openrouter_routing_mode if selection.provider == "openrouter" else "latency",
        selection.openrouter_provider_routing if selection.provider == "openrouter" else "default",
        selection.openrouter_selection_alias if selection.provider == "openrouter" else None,
        selection.connection_history,
        retained,
    )


@dataclass(frozen=True, slots=True)
class OperationalCodec:
    field: OperationalField
    command_type: type
    canonical_path: CanonicalPath
    kind: CodecKind

    def encode(self, command: OperationalStateCommand) -> CanonicalLeaf:
        if not isinstance(command, self.command_type):
            raise TypeError("command does not match operational field")
        value = command.value
        if self.kind == CodecKind.BOOLEAN and type(value) is not bool:
            raise TypeError("expected boolean")
        if self.kind == CodecKind.INTEGER and (type(value) is not int or value < 0):
            raise ValueError("expected non-negative integer")
        if self.kind == CodecKind.OPTIONAL_TEXT:
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("expected non-empty timestamp or null")
            value = value.strip() if isinstance(value, str) else None
            if value is not None:
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("expected ISO timestamp or null") from exc
        return self.canonical_path, value


OPERATIONAL_CODECS: Final = MappingProxyType(
    {
        OperationalField.GITHUB_STAR_CLICKED: OperationalCodec(
            OperationalField.GITHUB_STAR_CLICKED,
            GithubStarClickedCommand,
            ("github_star_prompt", "clicked"),
            CodecKind.BOOLEAN,
        ),
        OperationalField.GITHUB_STAR_LAST_SHOWN_AT: OperationalCodec(
            OperationalField.GITHUB_STAR_LAST_SHOWN_AT,
            GithubStarLastShownAtCommand,
            ("github_star_prompt", "last_shown_at"),
            CodecKind.OPTIONAL_TEXT,
        ),
        OperationalField.GITHUB_STAR_SHOW_COUNT: OperationalCodec(
            OperationalField.GITHUB_STAR_SHOW_COUNT,
            GithubStarShowCountCommand,
            ("github_star_prompt", "show_count"),
            CodecKind.INTEGER,
        ),
        OperationalField.GITHUB_STAR_TRANSLATION_SUCCESS_OBSERVED: OperationalCodec(
            OperationalField.GITHUB_STAR_TRANSLATION_SUCCESS_OBSERVED,
            GithubStarTranslationSuccessObservedCommand,
            ("github_star_prompt", "translation_success_observed"),
            CodecKind.BOOLEAN,
        ),
        OperationalField.GITHUB_STAR_ELIGIBLE_LAUNCH_COUNT: OperationalCodec(
            OperationalField.GITHUB_STAR_ELIGIBLE_LAUNCH_COUNT,
            GithubStarEligibleLaunchCountCommand,
            ("github_star_prompt", "eligible_launch_count"),
            CodecKind.INTEGER,
        ),
        OperationalField.PEER_TRANSLATION_EULA_ACCEPTED: OperationalCodec(
            OperationalField.PEER_TRANSLATION_EULA_ACCEPTED,
            PeerTranslationEulaAcceptedCommand,
            ("peer_translation", "eula_accepted"),
            CodecKind.BOOLEAN,
        ),
        OperationalField.INTEGRATED_CONTEXT_BOOTSTRAPPED: OperationalCodec(
            OperationalField.INTEGRATED_CONTEXT_BOOTSTRAPPED,
            IntegratedContextBootstrappedCommand,
            ("integrated_context", "bootstrapped"),
            CodecKind.BOOLEAN,
        ),
    }
)


__all__ = [
    "CanonicalProviderSelection",
    "CodecKind",
    "FIELD_CODECS",
    "FieldCodec",
    "OPERATIONAL_CODECS",
    "OperationalCodec",
    "OPENROUTER_SELECTION_ALIASES",
    "ProviderSelectionCommand",
    "ProviderDiscriminatorState",
    "convert_provider_selection",
    "project_provider_selection",
]
