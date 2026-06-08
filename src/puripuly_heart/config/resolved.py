from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, TypeAlias, cast

RuntimeChannel: TypeAlias = Literal["self", "peer"]
RUNTIME_CHANNEL_SELF: Final[RuntimeChannel] = "self"
RUNTIME_CHANNEL_PEER: Final[RuntimeChannel] = "peer"
RUNTIME_CHANNELS: Final[tuple[RuntimeChannel, ...]] = (
    RUNTIME_CHANNEL_SELF,
    RUNTIME_CHANNEL_PEER,
)

CredentialSource: TypeAlias = Literal["none", "secret_store", "managed"]
CREDENTIAL_SOURCE_NONE: Final[CredentialSource] = "none"
CREDENTIAL_SOURCE_SECRET_STORE: Final[CredentialSource] = "secret_store"
CREDENTIAL_SOURCE_MANAGED: Final[CredentialSource] = "managed"
CREDENTIAL_SOURCES: Final[tuple[CredentialSource, ...]] = (
    CREDENTIAL_SOURCE_NONE,
    CREDENTIAL_SOURCE_SECRET_STORE,
    CREDENTIAL_SOURCE_MANAGED,
)

ResolvedFeatureState: TypeAlias = Literal["enabled", "disabled"]
RESOLVED_FEATURE_ENABLED: Final[ResolvedFeatureState] = "enabled"
RESOLVED_FEATURE_DISABLED: Final[ResolvedFeatureState] = "disabled"
RESOLVED_FEATURE_STATES: Final[tuple[ResolvedFeatureState, ...]] = (
    RESOLVED_FEATURE_ENABLED,
    RESOLVED_FEATURE_DISABLED,
)

OverlayTarget: TypeAlias = Literal["steamvr", "desktop"]
OVERLAY_TARGET_STEAMVR: Final[OverlayTarget] = "steamvr"
OVERLAY_TARGET_DESKTOP: Final[OverlayTarget] = "desktop"
OVERLAY_TARGETS: Final[tuple[OverlayTarget, ...]] = (
    OVERLAY_TARGET_STEAMVR,
    OVERLAY_TARGET_DESKTOP,
)

ResolvedScalar: TypeAlias = str | int | float | bool | None
ResolvedOptionValue: TypeAlias = (
    ResolvedScalar | tuple["ResolvedOptionValue", ...] | Mapping[str, "ResolvedOptionValue"]
)

_RAW_SECRET_BEARING_OPTION_NAMES: Final = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential_value",
        "headers",
        "id_token",
        "managed_private_key",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_value",
        "session_token",
        "token",
        "token_value",
    }
)


def _empty_options() -> Mapping[str, ResolvedOptionValue]:
    return MappingProxyType({})


def _no_credential() -> ResolvedCredentialRequirement:
    return ResolvedCredentialRequirement(
        source=CREDENTIAL_SOURCE_NONE,
        required=False,
        reference=None,
    )


def _ensure_known_value(value: str, allowed: tuple[str, ...], *, field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {', '.join(allowed)}")


def _ensure_not_secret_bearing_name(name: str) -> None:
    normalized_name = name.strip().lower().replace("-", "_")
    if normalized_name in _RAW_SECRET_BEARING_OPTION_NAMES:
        raise ValueError(f"secret-bearing option name is not allowed: {name}")


def _freeze_option_mapping(values: Mapping[str, object]) -> Mapping[str, ResolvedOptionValue]:
    frozen: dict[str, ResolvedOptionValue] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise ValueError("resolved option keys must be strings")
        _ensure_not_secret_bearing_name(key)
        frozen[key] = _freeze_option_value(value)
    return MappingProxyType(frozen)


def _freeze_option_value(value: object) -> ResolvedOptionValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return _freeze_option_mapping(cast(Mapping[str, object], value))
    if isinstance(value, tuple | list):
        return tuple(_freeze_option_value(item) for item in value)
    raise TypeError("resolved option values must be scalars, mappings, lists, or tuples")


def _freeze_custom_terms(values: Mapping[str, object]) -> Mapping[str, tuple[str, ...]]:
    frozen: dict[str, tuple[str, ...]] = {}
    for language, terms in values.items():
        if not isinstance(language, str):
            raise ValueError("custom_terms keys must be strings")
        if isinstance(terms, str) or not isinstance(terms, tuple | list):
            raise ValueError("custom_terms values must be lists or tuples of strings")
        if not all(isinstance(term, str) for term in terms):
            raise ValueError("custom_terms values must contain only strings")
        frozen[language] = tuple(terms)
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class ResolvedCredentialRequirement:
    source: CredentialSource
    required: bool
    reference: str | None

    def __post_init__(self) -> None:
        _ensure_known_value(self.source, CREDENTIAL_SOURCES, field_name="source")
        if self.source == CREDENTIAL_SOURCE_NONE:
            if self.required or self.reference is not None:
                raise ValueError("none credential source cannot require a credential reference")
            return
        if self.required and not self.reference:
            raise ValueError("required credential sources must provide an adapter lookup reference")


@dataclass(frozen=True, slots=True)
class ResolvedLLMConfig:
    provider: str
    model: str
    credential: ResolvedCredentialRequirement = field(default_factory=_no_credential)
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_credential: ResolvedCredentialRequirement = field(default_factory=_no_credential)
    base_url: str | None = None
    service_endpoint: str | None = None
    region: str | None = None
    routing_mode: str | None = None
    provider_routing: str | None = None
    concurrency_limit: int = 1
    provider_options: Mapping[str, ResolvedOptionValue] = field(default_factory=_empty_options)

    def __post_init__(self) -> None:
        if self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be > 0")
        object.__setattr__(self, "provider_options", _freeze_option_mapping(self.provider_options))


@dataclass(frozen=True, slots=True)
class ResolvedSTTConfig:
    channel: RuntimeChannel
    provider: str
    model: str | None
    endpoint: str | None
    region: str | None
    credential: ResolvedCredentialRequirement
    input_host_api: str | None
    input_device: str | None
    output_device: str | None
    sample_rate_hz: int
    channels: int
    ring_buffer_ms: int
    drain_timeout_s: float
    vad_speech_threshold: float
    vad_hangover_ms: int
    vad_pre_roll_ms: int
    low_latency_enabled: bool
    low_latency_merge_gap_ms: int
    low_latency_spec_retry_max: int
    custom_vocabulary_enabled: bool
    custom_terms: Mapping[str, tuple[str, ...]]
    provider_options: Mapping[str, ResolvedOptionValue]

    def __post_init__(self) -> None:
        _ensure_known_value(self.channel, RUNTIME_CHANNELS, field_name="channel")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        if self.channels <= 0:
            raise ValueError("channels must be > 0")
        if self.ring_buffer_ms <= 0:
            raise ValueError("ring_buffer_ms must be > 0")
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.vad_hangover_ms < 0:
            raise ValueError("vad_hangover_ms must be >= 0")
        if self.vad_pre_roll_ms < 0:
            raise ValueError("vad_pre_roll_ms must be >= 0")
        if self.low_latency_merge_gap_ms < 0:
            raise ValueError("low_latency_merge_gap_ms must be >= 0")
        if self.low_latency_spec_retry_max < 0:
            raise ValueError("low_latency_spec_retry_max must be >= 0")
        object.__setattr__(self, "custom_terms", _freeze_custom_terms(self.custom_terms))
        object.__setattr__(self, "provider_options", _freeze_option_mapping(self.provider_options))


@dataclass(frozen=True, slots=True)
class ResolvedOverlayConfig:
    enabled: bool
    target: OverlayTarget
    show_translation: bool
    show_peer_original: bool
    calibration: Mapping[str, ResolvedOptionValue]
    desktop_overlay_options: Mapping[str, ResolvedOptionValue]

    def __post_init__(self) -> None:
        _ensure_known_value(self.target, OVERLAY_TARGETS, field_name="target")
        object.__setattr__(self, "calibration", _freeze_option_mapping(self.calibration))
        object.__setattr__(
            self,
            "desktop_overlay_options",
            _freeze_option_mapping(self.desktop_overlay_options),
        )


@dataclass(frozen=True, slots=True)
class ResolvedRuntimePolicy:
    translation: ResolvedFeatureState
    peer_translation: ResolvedFeatureState
    integrated_context: ResolvedFeatureState
    clipboard_auto_translate: ResolvedFeatureState
    llm_concurrency_limit: int
    policy_options: Mapping[str, ResolvedOptionValue]

    def __post_init__(self) -> None:
        _ensure_known_value(self.translation, RESOLVED_FEATURE_STATES, field_name="translation")
        _ensure_known_value(
            self.peer_translation,
            RESOLVED_FEATURE_STATES,
            field_name="peer_translation",
        )
        _ensure_known_value(
            self.integrated_context,
            RESOLVED_FEATURE_STATES,
            field_name="integrated_context",
        )
        _ensure_known_value(
            self.clipboard_auto_translate,
            RESOLVED_FEATURE_STATES,
            field_name="clipboard_auto_translate",
        )
        if self.llm_concurrency_limit <= 0:
            raise ValueError("llm_concurrency_limit must be > 0")
        object.__setattr__(self, "policy_options", _freeze_option_mapping(self.policy_options))


__all__ = [
    "CREDENTIAL_SOURCE_MANAGED",
    "CREDENTIAL_SOURCE_NONE",
    "CREDENTIAL_SOURCE_SECRET_STORE",
    "CREDENTIAL_SOURCES",
    "CredentialSource",
    "OVERLAY_TARGET_DESKTOP",
    "OVERLAY_TARGET_STEAMVR",
    "OVERLAY_TARGETS",
    "OverlayTarget",
    "RESOLVED_FEATURE_DISABLED",
    "RESOLVED_FEATURE_ENABLED",
    "RESOLVED_FEATURE_STATES",
    "RUNTIME_CHANNEL_PEER",
    "RUNTIME_CHANNEL_SELF",
    "RUNTIME_CHANNELS",
    "ResolvedCredentialRequirement",
    "ResolvedFeatureState",
    "ResolvedLLMConfig",
    "ResolvedOptionValue",
    "ResolvedOverlayConfig",
    "ResolvedRuntimePolicy",
    "ResolvedSTTConfig",
    "ResolvedScalar",
    "RuntimeChannel",
]
