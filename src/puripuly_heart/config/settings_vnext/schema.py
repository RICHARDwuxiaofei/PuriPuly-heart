from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from puripuly_heart.config.audio_host_api import WINDOWS_WASAPI_COMPATIBILITY_HOST_API
from puripuly_heart.config.overlay_calibration import OverlayCalibration

VNEXT_SETTINGS_SCHEMA_VERSION: Final = 25

DEFAULT_OPENROUTER_BROKER_BASE_URL: Final = "https://puripuly-heart-broker.kapitalismho.workers.dev"
DEFAULT_CUSTOM_VOCAB_TERMS: Final[Mapping[str, tuple[str, ...]]] = {
    "ko": ("아이리", "시나노"),
    "en": ("airi", "shinano"),
    "zh-CN": ("airi", "shinano"),
    "ja": ("airi", "shinano"),
}

RUNTIME_ONLY_LEGACY_SETTINGS_PATHS: Final = frozenset(
    {"ui.overlay_enabled", "ui.peer_translation_enabled"}
)

_LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS: Final = frozenset(
    {
        "model",
        "messages",
        "stream",
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "max_tokens",
    }
)
_LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEYS: Final = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "headers",
        "id_token",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "token",
    }
)
_LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEY_SUFFIXES: Final = (
    "_api_key",
    "_apikey",
    "_token",
    "_secret",
    "_password",
    "_private_key",
    "_credential",
    "_credential_value",
)
_LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEY_PREFIXES: Final = (
    "password_",
    "private_key_",
    "secret_",
)
_PROVIDER_VERIFICATION_STATUSES: Final = frozenset({"unknown", "verified", "failed", "skipped"})
_PROVIDER_VERIFICATION_SECRET_BEARING_KEY_FRAGMENTS: Final = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "body",
    "client_secret",
    "credential_value",
    "password",
    "payload",
    "private_key",
    "provider_payload",
    "raw",
    "raw_payload",
    "refresh_token",
    "request_body",
    "response_body",
    "secret",
    "token",
)


def _default_translation_connection_history() -> dict[str, str]:
    return {"gemma4": "managed"}


def _default_local_llm_extra_body() -> dict[str, object]:
    return {"reasoning_effort": "none"}


def _default_custom_terms() -> dict[str, list[str]]:
    return {language: list(terms) for language, terms in DEFAULT_CUSTOM_VOCAB_TERMS.items()}


def _normalize_extra_body_key(key: str) -> str:
    normalized = key.strip()
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", normalized)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    return re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()


def _is_secret_bearing_extra_body_key(key: str) -> bool:
    if key in _LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEYS:
        return True
    if "authorization" in key:
        return True
    return key.endswith(_LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEY_SUFFIXES) or key.startswith(
        _LOCAL_LLM_SECRET_BEARING_EXTRA_BODY_KEY_PREFIXES
    )


def _copy_local_llm_extra_body_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return _copy_local_llm_extra_body(value)
    if isinstance(value, list | tuple):
        return [_copy_local_llm_extra_body_value(item) for item in value]
    raise TypeError("local LLM extra_body values must be JSON-like scalars, mappings, or lists")


def _copy_local_llm_extra_body(values: Mapping[object, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str):
            raise ValueError("local LLM extra_body keys must be strings")
        key = _normalize_extra_body_key(raw_key)
        if key in _LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS:
            raise ValueError(f"reserved local LLM extra_body key is not allowed: {raw_key}")
        if _is_secret_bearing_extra_body_key(key):
            raise ValueError(f"secret-bearing local LLM extra_body key is not allowed: {raw_key}")
        copied[raw_key] = _copy_local_llm_extra_body_value(raw_value)
    try:
        json.dumps(copied, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("local LLM extra_body must be JSON serializable") from exc
    return copied


def _is_secret_bearing_provider_verification_metadata_key(key: str) -> bool:
    return any(fragment in key for fragment in _PROVIDER_VERIFICATION_SECRET_BEARING_KEY_FRAGMENTS)


def _copy_provider_verification_metadata_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError("provider verification metadata values must be JSON-like scalars")


def _copy_provider_verification_metadata(values: Mapping[object, object]) -> dict[str, object]:
    copied: dict[str, object] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str):
            raise ValueError("provider verification metadata keys must be strings")
        key = _normalize_extra_body_key(raw_key)
        if _is_secret_bearing_provider_verification_metadata_key(key):
            raise ValueError(
                f"secret-bearing provider verification metadata key is not allowed: {raw_key}"
            )
        copied[raw_key] = _copy_provider_verification_metadata_value(raw_value)
    try:
        json.dumps(copied, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider verification metadata must be JSON serializable") from exc
    return copied


def _optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"provider verification {field_name} must be a string or null")
    return value


def _required_provider_verification_string(value: object, *, field_name: str) -> str:
    raw = _optional_string(value, field_name=field_name)
    if raw is None:
        raise ValueError(f"provider verification evidence requires non-empty {field_name}")
    normalized = raw.strip()
    if not normalized:
        raise ValueError(f"provider verification evidence requires non-empty {field_name}")
    return normalized


def _optional_provider_verification_string(value: object, *, field_name: str) -> str | None:
    raw = _optional_string(value, field_name=field_name)
    if raw is None:
        return None
    normalized = raw.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class QwenTranslationIntent:
    region: str = "beijing"
    llm_model: str = "qwen3.5-plus"


@dataclass(frozen=True, slots=True)
class TranslationIntent:
    model: str = "gemma4"
    connection: str = "managed"
    connection_history: dict[str, str] = field(
        default_factory=_default_translation_connection_history
    )
    concurrency_limit: int = 5
    openrouter_fallback_selection_alias: str = "deepseek-v4-flash"
    openrouter_broker_base_url: str = DEFAULT_OPENROUTER_BROKER_BASE_URL
    openrouter_routing_mode: str = "latency"
    qwen: QwenTranslationIntent = field(default_factory=QwenTranslationIntent)


@dataclass(frozen=True, slots=True)
class LocalLLMIntent:
    backend: str = "ollama"
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.1:8b"
    extra_body: dict[str, object] = field(default_factory=_default_local_llm_extra_body)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_body", _copy_local_llm_extra_body(self.extra_body))


@dataclass(frozen=True, slots=True)
class DeepgramSTTIntent:
    model: str = "nova-3"


@dataclass(frozen=True, slots=True)
class QwenASRSTTIntent:
    model: str = "qwen3-asr-flash-realtime"


@dataclass(frozen=True, slots=True)
class SonioxSTTIntent:
    model: str = "stt-rt-v4"
    endpoint: str = "wss://stt-rt.soniox.com/transcribe-websocket"
    keepalive_interval_s: float = 10.0
    trailing_silence_ms: int = 100


@dataclass(frozen=True, slots=True)
class STTIntent:
    provider: str = "local_qwen"
    drain_timeout_s: float = 2.0
    vad_speech_threshold: float = 0.5
    low_latency_mode: bool = True
    low_latency_vad_hangover_ms: int = 600
    low_latency_merge_gap_ms: int = 600
    low_latency_spec_retry_max: int = 10
    custom_vocabulary_enabled: bool = True
    custom_terms: dict[str, list[str]] = field(default_factory=_default_custom_terms)
    deepgram: DeepgramSTTIntent = field(default_factory=DeepgramSTTIntent)
    qwen_asr: QwenASRSTTIntent = field(default_factory=QwenASRSTTIntent)
    soniox: SonioxSTTIntent = field(default_factory=SonioxSTTIntent)


@dataclass(frozen=True, slots=True)
class PeerSTTIntent:
    provider: str = "local_qwen"


@dataclass(frozen=True, slots=True)
class LanguageIntent:
    source_language: str = "ko"
    target_language: str = "en"
    peer_source_language: str = "en"
    peer_target_language: str = "ko"
    recent_source_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])
    recent_target_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])


@dataclass(frozen=True, slots=True)
class AudioIntent:
    ring_buffer_ms: int = 500
    input_host_api: str = WINDOWS_WASAPI_COMPATIBILITY_HOST_API
    input_device: str = ""


@dataclass(frozen=True, slots=True)
class DesktopAudioIntent:
    output_device: str = ""
    vad_speech_threshold: float = 0.6
    vad_hangover_ms: int = 500
    vad_pre_roll_ms: int = 500


@dataclass(frozen=True, slots=True)
class DesktopFletOverlayPositionIntent:
    x: int | float | None = None
    y: int | float | None = None


@dataclass(frozen=True, slots=True)
class DesktopFletOverlayVisualIntent:
    background_alpha: float = 0.6


@dataclass(frozen=True, slots=True)
class DesktopFletOverlayIntent:
    size_preset: str = "medium"
    position: DesktopFletOverlayPositionIntent = field(
        default_factory=DesktopFletOverlayPositionIntent
    )
    visual: DesktopFletOverlayVisualIntent = field(default_factory=DesktopFletOverlayVisualIntent)


@dataclass(frozen=True, slots=True)
class OverlayIntent:
    target: str = "steamvr"
    show_translation: bool = True
    show_peer_original: bool = True
    calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    desktop_flet: DesktopFletOverlayIntent = field(default_factory=DesktopFletOverlayIntent)


@dataclass(frozen=True, slots=True)
class OscIntent:
    host: str = "127.0.0.1"
    port: int = 9000
    chatbox_address: str = "/chatbox/input"
    chatbox_send: bool = True
    chatbox_clear: bool = False
    chatbox_max_chars: int = 144
    vrc_mic_intercept: bool = False
    chatbox_include_source: bool = False


@dataclass(frozen=True, slots=True)
class SecretsIntent:
    backend: str = "keyring"
    encrypted_file_path: str = "secrets.json"


@dataclass(frozen=True, slots=True)
class UiIntent:
    locale: str = "en"


@dataclass(frozen=True, slots=True)
class ClipboardIntent:
    auto_translate_enabled: bool = False


@dataclass(frozen=True, slots=True)
class IntegratedContextIntent:
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PromptIntent:
    system_prompt: str = ""


@dataclass(frozen=True, slots=True)
class UserIntentSettings:
    translation: TranslationIntent = field(default_factory=TranslationIntent)
    local_llm: LocalLLMIntent = field(default_factory=LocalLLMIntent)
    stt: STTIntent = field(default_factory=STTIntent)
    peer_stt: PeerSTTIntent = field(default_factory=PeerSTTIntent)
    languages: LanguageIntent = field(default_factory=LanguageIntent)
    audio: AudioIntent = field(default_factory=AudioIntent)
    desktop_audio: DesktopAudioIntent = field(default_factory=DesktopAudioIntent)
    overlay: OverlayIntent = field(default_factory=OverlayIntent)
    osc: OscIntent = field(default_factory=OscIntent)
    secrets: SecretsIntent = field(default_factory=SecretsIntent)
    ui: UiIntent = field(default_factory=UiIntent)
    clipboard: ClipboardIntent = field(default_factory=ClipboardIntent)
    integrated_context: IntegratedContextIntent = field(default_factory=IntegratedContextIntent)
    prompts: PromptIntent = field(default_factory=PromptIntent)


@dataclass(frozen=True, slots=True)
class ProviderVerificationEntry:
    status: str = "unknown"
    provider: str | None = None
    secret_key: str | None = None
    secret_revision: str | None = None
    secret_fingerprint: str | None = None
    verifier_context: dict[str, object] = field(default_factory=dict)
    verifier_evidence: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = str(self.status)
        if status not in _PROVIDER_VERIFICATION_STATUSES:
            raise ValueError(f"unsupported provider verification status: {self.status}")
        object.__setattr__(self, "status", status)
        if status == "unknown":
            object.__setattr__(self, "provider", None)
            object.__setattr__(self, "secret_key", None)
            object.__setattr__(self, "secret_revision", None)
            object.__setattr__(self, "secret_fingerprint", None)
            object.__setattr__(self, "verifier_context", {})
            object.__setattr__(self, "verifier_evidence", {})
            return

        provider = _required_provider_verification_string(self.provider, field_name="provider")
        secret_key = _required_provider_verification_string(
            self.secret_key,
            field_name="secret_key",
        )
        secret_revision = _optional_provider_verification_string(
            self.secret_revision,
            field_name="secret_revision",
        )
        secret_fingerprint = _optional_provider_verification_string(
            self.secret_fingerprint,
            field_name="secret_fingerprint",
        )
        verifier_context = _copy_provider_verification_metadata(self.verifier_context)
        if secret_revision is None and secret_fingerprint is None:
            raise ValueError(
                "provider verification evidence requires non-empty secret_revision or "
                "secret_fingerprint"
            )
        if not verifier_context:
            raise ValueError("provider verification evidence requires non-empty verifier_context")

        object.__setattr__(self, "provider", provider)
        object.__setattr__(
            self,
            "secret_key",
            secret_key,
        )
        object.__setattr__(
            self,
            "secret_revision",
            secret_revision,
        )
        object.__setattr__(
            self,
            "secret_fingerprint",
            secret_fingerprint,
        )
        object.__setattr__(
            self,
            "verifier_context",
            verifier_context,
        )
        object.__setattr__(
            self,
            "verifier_evidence",
            _copy_provider_verification_metadata(self.verifier_evidence),
        )


@dataclass(frozen=True, slots=True)
class ProviderVerificationState:
    deepgram: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    soniox: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    google: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    openrouter: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    deepseek: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    alibaba_beijing: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)
    alibaba_singapore: ProviderVerificationEntry = field(default_factory=ProviderVerificationEntry)


@dataclass(frozen=True, slots=True)
class ManagedConnectionState:
    installation_id: str = ""
    release_token: str | None = None
    release_token_expires_at: str | None = None
    verified_hardware_hash: str | None = None
    verified_hardware_hash_salt_version: int | None = None
    active_managed_credential_ref: str | None = None
    active_managed_expires_at: str | None = None
    founder_letter_seen_credential_ref: str | None = None
    referral_id: str | None = None


@dataclass(frozen=True, slots=True)
class GithubStarPromptState:
    clicked: bool = False
    last_shown_at: str | None = None
    show_count: int = 0
    translation_success_observed: bool = False
    eligible_launch_count: int = 0


@dataclass(frozen=True, slots=True)
class PeerTranslationState:
    eula_accepted: bool = False


@dataclass(frozen=True, slots=True)
class IntegratedContextState:
    bootstrapped: bool = False


@dataclass(frozen=True, slots=True)
class PersistedOperationalState:
    provider_verification: ProviderVerificationState = field(
        default_factory=ProviderVerificationState
    )
    managed_connection: ManagedConnectionState = field(default_factory=ManagedConnectionState)
    github_star_prompt: GithubStarPromptState = field(default_factory=GithubStarPromptState)
    peer_translation: PeerTranslationState = field(default_factory=PeerTranslationState)
    integrated_context: IntegratedContextState = field(default_factory=IntegratedContextState)


@dataclass(frozen=True, slots=True)
class AppSettingsVNext:
    settings_version: int = VNEXT_SETTINGS_SCHEMA_VERSION
    intent: UserIntentSettings = field(default_factory=UserIntentSettings)
    state: PersistedOperationalState = field(default_factory=PersistedOperationalState)


__all__ = [
    "AppSettingsVNext",
    "AudioIntent",
    "ClipboardIntent",
    "DeepgramSTTIntent",
    "DesktopAudioIntent",
    "DesktopFletOverlayIntent",
    "DesktopFletOverlayPositionIntent",
    "DesktopFletOverlayVisualIntent",
    "GithubStarPromptState",
    "IntegratedContextIntent",
    "IntegratedContextState",
    "LanguageIntent",
    "LocalLLMIntent",
    "ManagedConnectionState",
    "OscIntent",
    "OverlayIntent",
    "PeerSTTIntent",
    "PeerTranslationState",
    "PersistedOperationalState",
    "PromptIntent",
    "ProviderVerificationEntry",
    "ProviderVerificationState",
    "QwenASRSTTIntent",
    "QwenTranslationIntent",
    "RUNTIME_ONLY_LEGACY_SETTINGS_PATHS",
    "STTIntent",
    "SecretsIntent",
    "SonioxSTTIntent",
    "TranslationIntent",
    "UiIntent",
    "UserIntentSettings",
    "VNEXT_SETTINGS_SCHEMA_VERSION",
]
