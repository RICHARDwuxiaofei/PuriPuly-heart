from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from puripuly_heart.ui.overlay_calibration import OverlayCalibration

SETTINGS_SCHEMA_VERSION = 13
MAX_CUSTOM_VOCAB_TERMS = 100
DEFAULT_OPENROUTER_BROKER_BASE_URL = "https://puripuly-heart-broker.kapitalismho.workers.dev"
DEFAULT_CUSTOM_VOCAB_TERMS: dict[str, tuple[str, ...]] = {
    "ko": ("아이리", "시나노"),
    "en": ("airi", "shinano"),
    "zh-CN": ("airi", "shinano"),
}
LEGACY_QWEN_DEFAULT_PROMPT = (
    "VRChat social voice chat interpretation. Use spoken, conversational language and mirror "
    "the speaker's tone and formality. Fix voice recognition errors like missing punctuation "
    "and typos."
)


def _default_custom_terms() -> dict[str, list[str]]:
    return {language: list(terms) for language, terms in DEFAULT_CUSTOM_VOCAB_TERMS.items()}


class STTProviderName(str, Enum):
    LOCAL_QWEN = "local_qwen"
    DEEPGRAM = "deepgram"
    QWEN_ASR = "qwen_asr"
    SONIOX = "soniox"


class LLMProviderName(str, Enum):
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    QWEN = "qwen"


class SecretsBackend(str, Enum):
    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted_file"


class QwenRegion(str, Enum):
    BEIJING = "beijing"
    SINGAPORE = "singapore"


class GeminiLLMModel(str, Enum):
    GEMINI_3_FLASH = "gemini-3-flash-preview"
    GEMINI_31_FLASH_LITE = "gemini-3.1-flash-lite-preview"


class QwenLLMModel(str, Enum):
    QWEN_35_FLASH = "qwen3.5-flash"
    QWEN_35_PLUS = "qwen3.5-plus"


class OpenRouterLLMModel(str, Enum):
    GEMMA_4_26B_A4B_IT = "google/gemma-4-26b-a4b-it"


class OpenRouterRoutingMode(str, Enum):
    LATENCY = "latency"
    PARASAIL_FIRST = "parasail_first"
    NOVITA_FIRST = "novita_first"


class OpenRouterCredentialSource(str, Enum):
    NONE = "none"
    MANAGED = "managed"
    BYOK = "byok"


@dataclass(slots=True)
class LanguageSettings:
    source_language: str = "ko"
    target_language: str = "en"
    peer_source_language: str = ""
    peer_target_language: str = ""
    recent_source_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])
    recent_target_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])

    def validate(self) -> None:
        if not self.source_language:
            raise ValueError("source_language must be non-empty")
        if not self.target_language:
            raise ValueError("target_language must be non-empty")

    @property
    def effective_peer_source(self) -> str:
        return self.peer_source_language or self.source_language

    @property
    def effective_peer_target(self) -> str:
        return self.peer_target_language or self.target_language


@dataclass(slots=True)
class AudioSettings:
    internal_sample_rate_hz: int = 16000
    internal_channels: int = 1
    ring_buffer_ms: int = 500
    input_host_api: str = "Windows DirectSound"
    input_device: str = ""

    def validate(self) -> None:
        if self.internal_sample_rate_hz not in (8000, 16000):
            raise ValueError("internal_sample_rate_hz must be 8000 or 16000")
        if self.internal_channels != 1:
            raise ValueError("internal_channels must be 1 (mono)")
        if self.ring_buffer_ms <= 0:
            raise ValueError("ring_buffer_ms must be > 0")
        if self.input_host_api is None:
            raise ValueError("input_host_api must be a string")
        if self.input_device is None:
            raise ValueError("input_device must be a string")


@dataclass(slots=True)
class DesktopAudioSettings:
    output_device: str = ""
    vad_speech_threshold: float = 0.6
    vad_hangover_ms: int = 600
    vad_pre_roll_ms: int = 500

    def validate(self) -> None:
        if self.output_device is None:
            raise ValueError("output_device must be a string")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.vad_hangover_ms < 0:
            raise ValueError("vad_hangover_ms must be >= 0")
        if self.vad_pre_roll_ms < 0:
            raise ValueError("vad_pre_roll_ms must be >= 0")


@dataclass(slots=True)
class STTSettings:
    drain_timeout_s: float = 2.0
    vad_speech_threshold: float = 0.5
    low_latency_mode: bool = True
    low_latency_vad_hangover_ms: int = 600
    low_latency_merge_gap_ms: int = 600
    low_latency_spec_retry_max: int = 10
    custom_vocabulary_enabled: bool = True
    custom_terms: dict[str, list[str]] = field(default_factory=_default_custom_terms)

    def validate(self) -> None:
        if self.drain_timeout_s <= 0:
            raise ValueError("drain_timeout_s must be > 0")
        if not (0.0 <= self.vad_speech_threshold <= 1.0):
            raise ValueError("vad_speech_threshold must be in 0.0..1.0")
        if self.low_latency_vad_hangover_ms < 0:
            raise ValueError("low_latency_vad_hangover_ms must be >= 0")
        if self.low_latency_merge_gap_ms < 0:
            raise ValueError("low_latency_merge_gap_ms must be >= 0")
        if self.low_latency_spec_retry_max < 0:
            raise ValueError("low_latency_spec_retry_max must be >= 0")
        if not isinstance(self.custom_vocabulary_enabled, bool):
            raise ValueError("custom_vocabulary_enabled must be a bool")
        if not isinstance(self.custom_terms, dict):
            raise ValueError("custom_terms must be a dict[str, list[str]]")
        for language, terms in self.custom_terms.items():
            if not isinstance(language, str):
                raise ValueError("custom_terms keys must be strings")
            if not isinstance(terms, list):
                raise ValueError("custom_terms values must be lists of strings")
            for term in terms:
                if not isinstance(term, str):
                    raise ValueError("custom_terms values must be lists of strings")


@dataclass(slots=True)
class DeepgramSTTSettings:
    model: str = "nova-3"

    def validate(self) -> None:
        if not self.model:
            raise ValueError("model must be non-empty")


@dataclass(slots=True)
class QwenASRSTTSettings:
    model: str = "qwen3-asr-flash-realtime"
    endpoint: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    def validate(self) -> None:
        if not self.model:
            raise ValueError("model must be non-empty")
        if not self.endpoint:
            raise ValueError("endpoint must be non-empty")


@dataclass(slots=True)
class SonioxSTTSettings:
    model: str = "stt-rt-v4"
    endpoint: str = "wss://stt-rt.soniox.com/transcribe-websocket"
    keepalive_interval_s: float = 10.0
    trailing_silence_ms: int = 100

    def validate(self) -> None:
        if not self.model:
            raise ValueError("model must be non-empty")
        if not self.endpoint:
            raise ValueError("endpoint must be non-empty")
        if self.keepalive_interval_s <= 0:
            raise ValueError("keepalive_interval_s must be > 0")
        if self.trailing_silence_ms < 0:
            raise ValueError("trailing_silence_ms must be >= 0")


@dataclass(slots=True)
class PeerQwenASRSTTSettings:
    model: str | None = None
    region: QwenRegion | None = None

    def validate(self) -> None:
        if self.model is not None and not self.model:
            raise ValueError("peer qwen asr model override must be non-empty")
        if self.region is not None and not isinstance(self.region, QwenRegion):
            raise ValueError("invalid peer qwen asr region")


@dataclass(slots=True)
class PeerSonioxSTTSettings:
    model: str | None = None
    endpoint: str | None = None
    keepalive_interval_s: float | None = None
    trailing_silence_ms: int | None = None

    def validate(self) -> None:
        if self.model is not None and not self.model:
            raise ValueError("peer soniox model override must be non-empty")
        if self.endpoint is not None and not self.endpoint:
            raise ValueError("peer soniox endpoint override must be non-empty")
        if self.keepalive_interval_s is not None and self.keepalive_interval_s <= 0:
            raise ValueError("peer soniox keepalive override must be > 0")
        if self.trailing_silence_ms is not None and self.trailing_silence_ms < 0:
            raise ValueError("peer soniox trailing silence override must be >= 0")


@dataclass(slots=True)
class LLMSettings:
    concurrency_limit: int = 5

    def validate(self) -> None:
        if self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be > 0")


@dataclass(slots=True)
class OSCSettings:
    host: str = "127.0.0.1"
    port: int = 9000
    chatbox_address: str = "/chatbox/input"
    chatbox_send: bool = True
    chatbox_clear: bool = False
    chatbox_max_chars: int = 144
    cooldown_s: float = 1.5
    ttl_s: float = 7.0
    vrc_mic_intercept: bool = False
    chatbox_include_source: bool = True

    def validate(self) -> None:
        if not self.host:
            raise ValueError("host must be non-empty")
        if not (0 < self.port <= 65535):
            raise ValueError("port must be in 1..65535")
        if not self.chatbox_address or not self.chatbox_address.startswith("/"):
            raise ValueError("chatbox_address must start with '/'")
        if self.chatbox_max_chars <= 0:
            raise ValueError("chatbox_max_chars must be > 0")
        if self.cooldown_s <= 0:
            raise ValueError("cooldown_s must be > 0")
        if self.ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")


@dataclass(slots=True)
class ProviderSettings:
    stt: STTProviderName = STTProviderName.LOCAL_QWEN
    peer_stt: STTProviderName = STTProviderName.DEEPGRAM
    llm: LLMProviderName = LLMProviderName.GEMINI

    def validate(self) -> None:
        if not isinstance(self.stt, STTProviderName):
            raise ValueError("invalid stt provider")
        if not isinstance(self.peer_stt, STTProviderName):
            raise ValueError("invalid peer stt provider")
        if not isinstance(self.llm, LLMProviderName):
            raise ValueError("invalid llm provider")


@dataclass(slots=True)
class SecretsSettings:
    backend: SecretsBackend = SecretsBackend.KEYRING
    encrypted_file_path: str = "secrets.json"

    def validate(self) -> None:
        if not isinstance(self.backend, SecretsBackend):
            raise ValueError("invalid secrets backend")
        if self.backend == SecretsBackend.ENCRYPTED_FILE and not self.encrypted_file_path:
            raise ValueError("encrypted_file_path must be set for encrypted_file backend")


@dataclass(slots=True)
class GeminiSettings:
    llm_model: GeminiLLMModel = GeminiLLMModel.GEMINI_31_FLASH_LITE

    def validate(self) -> None:
        if not isinstance(self.llm_model, GeminiLLMModel):
            raise ValueError("invalid gemini llm model")


@dataclass(slots=True)
class QwenSettings:
    region: QwenRegion = QwenRegion.BEIJING
    llm_model: QwenLLMModel = QwenLLMModel.QWEN_35_PLUS

    def validate(self) -> None:
        if not isinstance(self.region, QwenRegion):
            raise ValueError("invalid qwen region")
        if not isinstance(self.llm_model, QwenLLMModel):
            raise ValueError("invalid qwen llm model")

    def get_llm_base_url(self) -> str:
        if self.region == QwenRegion.BEIJING:
            return "https://dashscope.aliyuncs.com/api/v1"
        return "https://dashscope-intl.aliyuncs.com/api/v1"

    def get_asr_endpoint(self) -> str:
        if self.region == QwenRegion.BEIJING:
            return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"


@dataclass(slots=True)
class OpenRouterSettings:
    llm_model: OpenRouterLLMModel = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    routing_mode: OpenRouterRoutingMode = OpenRouterRoutingMode.LATENCY
    selected_source: OpenRouterCredentialSource = OpenRouterCredentialSource.NONE
    broker_base_url: str = DEFAULT_OPENROUTER_BROKER_BASE_URL

    def validate(self) -> None:
        if not isinstance(self.llm_model, OpenRouterLLMModel):
            raise ValueError("invalid openrouter llm model")
        if not isinstance(self.routing_mode, OpenRouterRoutingMode):
            raise ValueError("invalid openrouter routing mode")
        if not isinstance(self.selected_source, OpenRouterCredentialSource):
            raise ValueError("invalid openrouter credential source")
        if not isinstance(self.broker_base_url, str) or not self.broker_base_url.strip():
            raise ValueError("invalid openrouter broker base url")


@dataclass(slots=True)
class UiSettings:
    locale: str = "en"
    # Session-only toggle; intentionally not persisted to settings.json.
    overlay_enabled: bool = False
    show_overlay_translation: bool = True
    show_overlay_peer_original: bool = True
    peer_translation_enabled: bool = False
    integrated_context_enabled: bool = False
    integrated_context_bootstrapped: bool = False

    def validate(self) -> None:
        if not self.locale:
            raise ValueError("locale must be non-empty")


@dataclass(slots=True)
class ApiKeyVerificationSettings:
    """Stores API key verification status for each provider."""

    deepgram: bool = False
    soniox: bool = False
    google: bool = False
    openrouter: bool = False
    alibaba_beijing: bool = False
    alibaba_singapore: bool = False

    def validate(self) -> None:
        pass  # No validation needed


@dataclass(slots=True)
class ManagedIdentitySettings:
    installation_id: str = ""
    release_token: str | None = None
    release_token_expires_at: str | None = None
    verified_hardware_hash: str | None = None
    verified_hardware_hash_salt_version: int | None = None

    def validate(self) -> None:
        if not isinstance(self.installation_id, str):
            raise ValueError("managed installation_id must be a string")
        if self.release_token is not None and not isinstance(self.release_token, str):
            raise ValueError("managed release_token must be a string or None")
        if self.release_token_expires_at is not None and not isinstance(
            self.release_token_expires_at, str
        ):
            raise ValueError("managed release_token_expires_at must be a string or None")
        if self.verified_hardware_hash is not None and not isinstance(
            self.verified_hardware_hash, str
        ):
            raise ValueError("managed verified_hardware_hash must be a string or None")
        if isinstance(self.verified_hardware_hash_salt_version, bool) or (
            self.verified_hardware_hash_salt_version is not None
            and not isinstance(self.verified_hardware_hash_salt_version, int)
        ):
            raise ValueError("managed verified_hardware_hash_salt_version must be an int or None")


@dataclass(slots=True)
class AppSettings:
    settings_version: int = SETTINGS_SCHEMA_VERSION
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    languages: LanguageSettings = field(default_factory=LanguageSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    desktop_audio: DesktopAudioSettings = field(default_factory=DesktopAudioSettings)
    overlay_calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    stt: STTSettings = field(default_factory=STTSettings)
    deepgram_stt: DeepgramSTTSettings = field(default_factory=DeepgramSTTSettings)
    qwen_asr_stt: QwenASRSTTSettings = field(default_factory=QwenASRSTTSettings)
    soniox_stt: SonioxSTTSettings = field(default_factory=SonioxSTTSettings)
    peer_qwen_asr_stt: PeerQwenASRSTTSettings = field(default_factory=PeerQwenASRSTTSettings)
    peer_soniox_stt: PeerSonioxSTTSettings = field(default_factory=PeerSonioxSTTSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    openrouter: OpenRouterSettings = field(default_factory=OpenRouterSettings)
    qwen: QwenSettings = field(default_factory=QwenSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    osc: OSCSettings = field(default_factory=OSCSettings)
    secrets: SecretsSettings = field(default_factory=SecretsSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    api_key_verified: ApiKeyVerificationSettings = field(default_factory=ApiKeyVerificationSettings)
    managed_identity: ManagedIdentitySettings = field(default_factory=ManagedIdentitySettings)
    system_prompt: str = ""
    system_prompts: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.settings_version <= 0:
            raise ValueError("settings_version must be > 0")
        self.provider.validate()
        self.languages.validate()
        self.audio.validate()
        self.desktop_audio.validate()
        self.overlay_calibration.validate()
        self.stt.validate()
        self.deepgram_stt.validate()
        self.qwen_asr_stt.validate()
        self.soniox_stt.validate()
        self.peer_qwen_asr_stt.validate()
        self.peer_soniox_stt.validate()
        self.gemini.validate()
        self.openrouter.validate()
        self.qwen.validate()
        self.llm.validate()
        self.osc.validate()
        self.secrets.validate()
        self.ui.validate()
        self.api_key_verified.validate()
        self.managed_identity.validate()
        for key, value in self.system_prompts.items():
            if not isinstance(key, str):
                raise ValueError("system_prompts keys must be strings")
            if not isinstance(value, str):
                raise ValueError("system_prompts values must be strings")


def _enum_to_value(obj: object) -> object:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _enum_to_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enum_to_value(v) for v in obj]
    return obj


def to_dict(settings: AppSettings) -> dict[str, Any]:
    data: dict[str, Any] = {
        "settings_version": settings.settings_version,
        "provider": {
            "stt": settings.provider.stt.value,
            "peer_stt": settings.provider.peer_stt.value,
            "llm": settings.provider.llm.value,
        },
        "languages": {
            "source_language": settings.languages.source_language,
            "target_language": settings.languages.target_language,
            "peer_source_language": settings.languages.peer_source_language,
            "peer_target_language": settings.languages.peer_target_language,
            "recent_source_languages": settings.languages.recent_source_languages,
            "recent_target_languages": settings.languages.recent_target_languages,
        },
        "audio": {
            "internal_sample_rate_hz": settings.audio.internal_sample_rate_hz,
            "internal_channels": settings.audio.internal_channels,
            "ring_buffer_ms": settings.audio.ring_buffer_ms,
            "input_host_api": settings.audio.input_host_api,
            "input_device": settings.audio.input_device,
        },
        "desktop_audio": {
            "output_device": settings.desktop_audio.output_device,
            "vad_speech_threshold": settings.desktop_audio.vad_speech_threshold,
            "vad_hangover_ms": settings.desktop_audio.vad_hangover_ms,
            "vad_pre_roll_ms": settings.desktop_audio.vad_pre_roll_ms,
        },
        "overlay_calibration": settings.overlay_calibration.to_dict(),
        "stt": {
            "drain_timeout_s": settings.stt.drain_timeout_s,
            "vad_speech_threshold": settings.stt.vad_speech_threshold,
            "low_latency_mode": settings.stt.low_latency_mode,
            "low_latency_vad_hangover_ms": settings.stt.low_latency_vad_hangover_ms,
            "low_latency_merge_gap_ms": settings.stt.low_latency_merge_gap_ms,
            "low_latency_spec_retry_max": settings.stt.low_latency_spec_retry_max,
            "custom_vocabulary_enabled": settings.stt.custom_vocabulary_enabled,
            "custom_terms": _parse_custom_terms(settings.stt.custom_terms),
        },
        "deepgram_stt": {
            "model": settings.deepgram_stt.model,
        },
        "qwen_asr_stt": {
            "model": settings.qwen_asr_stt.model,
            "endpoint": settings.qwen.get_asr_endpoint(),
        },
        "soniox_stt": {
            "model": settings.soniox_stt.model,
            "endpoint": settings.soniox_stt.endpoint,
            "keepalive_interval_s": settings.soniox_stt.keepalive_interval_s,
            "trailing_silence_ms": settings.soniox_stt.trailing_silence_ms,
        },
        "peer_qwen_asr_stt": {
            "model": settings.peer_qwen_asr_stt.model,
            "region": (
                settings.peer_qwen_asr_stt.region.value
                if settings.peer_qwen_asr_stt.region is not None
                else None
            ),
        },
        "peer_soniox_stt": {
            "model": settings.peer_soniox_stt.model,
            "endpoint": settings.peer_soniox_stt.endpoint,
            "keepalive_interval_s": settings.peer_soniox_stt.keepalive_interval_s,
            "trailing_silence_ms": settings.peer_soniox_stt.trailing_silence_ms,
        },
        "gemini": {
            "llm_model": settings.gemini.llm_model.value,
        },
        "openrouter": {
            "llm_model": settings.openrouter.llm_model.value,
            "routing_mode": settings.openrouter.routing_mode.value,
            "selected_source": settings.openrouter.selected_source.value,
            "broker_base_url": settings.openrouter.broker_base_url,
        },
        "qwen": {
            "region": settings.qwen.region.value,
            "llm_model": settings.qwen.llm_model.value,
        },
        "llm": {"concurrency_limit": settings.llm.concurrency_limit},
        "osc": {
            "host": settings.osc.host,
            "port": settings.osc.port,
            "chatbox_address": settings.osc.chatbox_address,
            "chatbox_send": settings.osc.chatbox_send,
            "chatbox_clear": settings.osc.chatbox_clear,
            "chatbox_max_chars": settings.osc.chatbox_max_chars,
            "cooldown_s": settings.osc.cooldown_s,
            "ttl_s": settings.osc.ttl_s,
            "vrc_mic_intercept": settings.osc.vrc_mic_intercept,
            "chatbox_include_source": settings.osc.chatbox_include_source,
        },
        "secrets": {
            "backend": settings.secrets.backend.value,
            "encrypted_file_path": settings.secrets.encrypted_file_path,
        },
        "ui": {
            "locale": settings.ui.locale,
            "show_overlay_translation": settings.ui.show_overlay_translation,
            "show_overlay_peer_original": settings.ui.show_overlay_peer_original,
            "peer_translation_enabled": settings.ui.peer_translation_enabled,
            "integrated_context_enabled": settings.ui.integrated_context_enabled,
            "integrated_context_bootstrapped": settings.ui.integrated_context_bootstrapped,
        },
        "api_key_verified": {
            "deepgram": settings.api_key_verified.deepgram,
            "soniox": settings.api_key_verified.soniox,
            "google": settings.api_key_verified.google,
            "openrouter": settings.api_key_verified.openrouter,
            "alibaba_beijing": settings.api_key_verified.alibaba_beijing,
            "alibaba_singapore": settings.api_key_verified.alibaba_singapore,
        },
        "managed_identity": {
            "installation_id": settings.managed_identity.installation_id,
            "release_token": settings.managed_identity.release_token,
            "release_token_expires_at": settings.managed_identity.release_token_expires_at,
            "verified_hardware_hash": settings.managed_identity.verified_hardware_hash,
            "verified_hardware_hash_salt_version": (
                settings.managed_identity.verified_hardware_hash_salt_version
            ),
        },
        "system_prompt": settings.system_prompt,
        "system_prompts": settings.system_prompts,
    }
    return _enum_to_value(data)  # type: ignore[return-value]


def _parse_stt_provider(value: str) -> STTProviderName:
    """Parse STT provider, mapping legacy values to supported providers."""
    if value == "alibaba":
        return STTProviderName.QWEN_ASR
    try:
        return STTProviderName(value)
    except ValueError:
        return STTProviderName.DEEPGRAM


def _parse_llm_provider(value: object) -> LLMProviderName:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return LLMProviderName(normalized)
        except ValueError:
            pass
    return LLMProviderName.GEMINI


def _parse_qwen_llm_model(value: object) -> QwenLLMModel:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized == "qwen-mt-flash":
            normalized = QwenLLMModel.QWEN_35_PLUS.value
        try:
            return QwenLLMModel(normalized)
        except ValueError:
            pass
    return QwenLLMModel.QWEN_35_PLUS


def _parse_gemini_llm_model(value: object) -> GeminiLLMModel:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized == "gemini-3-flash":
            normalized = GeminiLLMModel.GEMINI_3_FLASH.value
        elif normalized == "gemini-3.1-flash-lite":
            normalized = GeminiLLMModel.GEMINI_31_FLASH_LITE.value
        try:
            return GeminiLLMModel(normalized)
        except ValueError:
            pass
    return GeminiLLMModel.GEMINI_31_FLASH_LITE


def _parse_openrouter_llm_model(value: object) -> OpenRouterLLMModel:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return OpenRouterLLMModel(normalized)
        except ValueError:
            pass
    return OpenRouterLLMModel.GEMMA_4_26B_A4B_IT


def _parse_openrouter_routing_mode(value: object) -> OpenRouterRoutingMode:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return OpenRouterRoutingMode(normalized)
        except ValueError:
            pass
    return OpenRouterRoutingMode.LATENCY


def _parse_openrouter_credential_source(
    value: object,
    *,
    fallback: OpenRouterCredentialSource = OpenRouterCredentialSource.NONE,
) -> OpenRouterCredentialSource:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return OpenRouterCredentialSource(normalized)
        except ValueError:
            pass
    return fallback


def _parse_openrouter_broker_base_url(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return DEFAULT_OPENROUTER_BROKER_BASE_URL


def _default_openrouter_credential_source_value(data: dict[str, Any]) -> OpenRouterCredentialSource:
    provider_data = data.get("provider")
    provider_llm_value = (
        provider_data.get("llm", LLMProviderName.GEMINI.value)
        if isinstance(provider_data, dict)
        else LLMProviderName.GEMINI.value
    )
    if _parse_llm_provider(provider_llm_value) == LLMProviderName.OPENROUTER:
        return OpenRouterCredentialSource.BYOK
    return OpenRouterCredentialSource.NONE


def _get_raw_openrouter_selected_source(openrouter_data: dict[str, Any]) -> object:
    if "selected_source" in openrouter_data:
        return openrouter_data["selected_source"]
    if "credential_source" in openrouter_data:
        return openrouter_data["credential_source"]
    if "selected_credential_source" in openrouter_data:
        return openrouter_data["selected_credential_source"]
    return None


def _infer_qwen_region_from_legacy_asr_endpoint(value: object) -> QwenRegion | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if "dashscope-intl.aliyuncs.com" in normalized:
        return QwenRegion.SINGAPORE
    if "dashscope.aliyuncs.com" in normalized:
        return QwenRegion.BEIJING
    return None


def _parse_qwen_region(value: object, *, legacy_asr_endpoint: object = None) -> QwenRegion:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            return QwenRegion(normalized)
        except ValueError:
            pass
    inferred = _infer_qwen_region_from_legacy_asr_endpoint(legacy_asr_endpoint)
    if inferred is not None:
        return inferred
    return QwenRegion.BEIJING


def _llm_prompt_key(provider: LLMProviderName) -> str:
    if provider == LLMProviderName.GEMINI:
        return "gemini"
    if provider == LLMProviderName.OPENROUTER:
        return "openrouter"
    return "qwen"


def _parse_system_prompts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, prompt in value.items():
        if isinstance(key, str) and isinstance(prompt, str):
            out[key] = prompt
    return out


def _parse_custom_terms(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("custom_terms must be a dict[str, list[str]]")

    out: dict[str, list[str]] = {}
    for language, terms in value.items():
        if not isinstance(language, str):
            raise ValueError("custom_terms keys must be strings")
        if not isinstance(terms, list):
            raise ValueError("custom_terms values must be lists of strings")

        normalized_terms: list[str] = []
        seen_terms: set[str] = set()
        for term in terms:
            if not isinstance(term, str):
                raise ValueError("custom_terms values must be lists of strings")
            normalized_term = term.strip()
            if not normalized_term or normalized_term in seen_terms:
                continue
            if len(normalized_terms) >= MAX_CUSTOM_VOCAB_TERMS:
                break
            seen_terms.add(normalized_term)
            normalized_terms.append(normalized_term)

        out[language] = normalized_terms
    return out


def _coerce_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _parse_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize_peer_block(data: dict[str, Any], key: str, default_block: dict[str, Any]) -> bool:
    if isinstance(data.get(key), dict):
        return False
    data[key] = copy.deepcopy(default_block)
    return True


def _migrate_settings_dict(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    data: dict[str, Any] = copy.deepcopy(raw)
    changed = False
    peer_block_defaults: dict[str, dict[str, Any]] = {
        "peer_qwen_asr_stt": {"model": None, "region": None},
        "peer_soniox_stt": {
            "model": None,
            "endpoint": None,
            "keepalive_interval_s": None,
            "trailing_silence_ms": None,
        },
    }

    version = _coerce_int(data.get("settings_version"), 1)
    if version < 1:
        version = 1

    if version < 2:
        llm_data = data.get("llm")
        if not isinstance(llm_data, dict):
            llm_data = {}
            data["llm"] = llm_data
            changed = True

        concurrency_limit = _coerce_int(llm_data.get("concurrency_limit"), 1)
        # Preserve explicit custom limits (>1), migrate legacy default 1 to new default 2.
        if concurrency_limit <= 1:
            llm_data["concurrency_limit"] = 2
            changed = True

        version = 2

    if version < 3:
        desktop_audio_data = data.get("desktop_audio")
        if not isinstance(desktop_audio_data, dict):
            desktop_audio_data = {}
            data["desktop_audio"] = desktop_audio_data
            changed = True
        if desktop_audio_data.get("vad_speech_threshold") != 0.6:
            desktop_audio_data["vad_speech_threshold"] = 0.6
            changed = True
        version = 3

    if version < 4:
        raw_provider_data = data.get("provider")
        if raw_provider_data is None:
            provider_data = {}
            data["provider"] = provider_data
            changed = True
        elif isinstance(raw_provider_data, dict):
            provider_data = raw_provider_data
        else:
            provider_data = {
                "stt": STTProviderName.DEEPGRAM.value,
                "llm": LLMProviderName.GEMINI.value,
            }
            data["provider"] = provider_data
            changed = True

        if "peer_stt" not in provider_data:
            provider_data["peer_stt"] = STTProviderName.DEEPGRAM.value
            changed = True

        for key, default_block in peer_block_defaults.items():
            if _normalize_peer_block(data, key, default_block):
                changed = True

        version = 4

    if version < 5:
        openrouter_data = data.get("openrouter")
        if not isinstance(openrouter_data, dict):
            data["openrouter"] = {
                "llm_model": OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value,
            }
            changed = True

        api_key_verified = data.get("api_key_verified")
        if not isinstance(api_key_verified, dict):
            api_key_verified = {}
            data["api_key_verified"] = api_key_verified
            changed = True
        if "openrouter" not in api_key_verified:
            api_key_verified["openrouter"] = False
            changed = True

        version = 5

    if version < 6:
        llm_data = data.get("llm")
        if not isinstance(llm_data, dict):
            llm_data = {}
            data["llm"] = llm_data
            changed = True

        concurrency_limit = _coerce_int(llm_data.get("concurrency_limit"), 2)
        # Migrate previous default-sized limits up to the faster default while preserving
        # explicit higher custom values.
        if concurrency_limit <= 2:
            llm_data["concurrency_limit"] = 5
            changed = True

        version = 6

    if version < 7:
        desktop_audio_data = data.get("desktop_audio")
        if (
            isinstance(desktop_audio_data, dict)
            and desktop_audio_data.get("vad_hangover_ms") == 900
        ):
            desktop_audio_data["vad_hangover_ms"] = 700
            changed = True

        version = 7

    if version < 8:
        desktop_audio_data = data.get("desktop_audio")
        if (
            isinstance(desktop_audio_data, dict)
            and desktop_audio_data.get("vad_hangover_ms") == 700
        ):
            desktop_audio_data["vad_hangover_ms"] = 600
            changed = True

        version = 8

    if version < 9:
        managed_identity_data = data.get("managed_identity")
        if not isinstance(managed_identity_data, dict):
            managed_identity_data = {}
            data["managed_identity"] = managed_identity_data
            changed = True

        if "installation_id" not in managed_identity_data:
            managed_identity_data["installation_id"] = ""
            changed = True
        if "release_token" not in managed_identity_data:
            managed_identity_data["release_token"] = None
            changed = True
        if "release_token_expires_at" not in managed_identity_data:
            managed_identity_data["release_token_expires_at"] = None
            changed = True

        version = 9

    if version < 10:
        openrouter_data = data.get("openrouter")
        if not isinstance(openrouter_data, dict):
            openrouter_data = {}
            data["openrouter"] = openrouter_data
            changed = True

        raw_selected_source = _get_raw_openrouter_selected_source(openrouter_data)
        normalized_selected_source = _parse_openrouter_credential_source(
            raw_selected_source,
            fallback=_default_openrouter_credential_source_value(data),
        )
        if openrouter_data.get("selected_source") != normalized_selected_source.value:
            openrouter_data["selected_source"] = normalized_selected_source.value
            changed = True
        if "credential_source" in openrouter_data:
            del openrouter_data["credential_source"]
            changed = True
        if "selected_credential_source" in openrouter_data:
            del openrouter_data["selected_credential_source"]
            changed = True

        version = 10

    if version < 11:
        openrouter_data = data.get("openrouter")
        if not isinstance(openrouter_data, dict):
            openrouter_data = {}
            data["openrouter"] = openrouter_data
            changed = True

        normalized_selected_source = _parse_openrouter_credential_source(
            _get_raw_openrouter_selected_source(openrouter_data),
            fallback=_default_openrouter_credential_source_value(data),
        )
        if (
            _default_openrouter_credential_source_value(data) == OpenRouterCredentialSource.BYOK
            and normalized_selected_source == OpenRouterCredentialSource.NONE
        ):
            normalized_selected_source = OpenRouterCredentialSource.BYOK
        if openrouter_data.get("selected_source") != normalized_selected_source.value:
            openrouter_data["selected_source"] = normalized_selected_source.value
            changed = True
        if "credential_source" in openrouter_data:
            del openrouter_data["credential_source"]
            changed = True
        if "selected_credential_source" in openrouter_data:
            del openrouter_data["selected_credential_source"]
            changed = True

        version = 11

    if version < 12:
        openrouter_data = data.get("openrouter")
        if not isinstance(openrouter_data, dict):
            openrouter_data = {}
            data["openrouter"] = openrouter_data
            changed = True

        normalized_broker_base_url = _parse_openrouter_broker_base_url(
            openrouter_data.get("broker_base_url")
        )
        if openrouter_data.get("broker_base_url") != normalized_broker_base_url:
            openrouter_data["broker_base_url"] = normalized_broker_base_url
            changed = True

        version = 12

    if version < 13:
        managed_identity_data = data.get("managed_identity")
        if not isinstance(managed_identity_data, dict):
            managed_identity_data = {}
            data["managed_identity"] = managed_identity_data
            changed = True

        if "verified_hardware_hash" not in managed_identity_data:
            managed_identity_data["verified_hardware_hash"] = None
            changed = True
        if "verified_hardware_hash_salt_version" not in managed_identity_data:
            managed_identity_data["verified_hardware_hash_salt_version"] = None
            changed = True

        version = 13

    stt_data = data.get("stt")
    if not isinstance(stt_data, dict):
        stt_data = {}
        data["stt"] = stt_data
        changed = True

    if "custom_terms" not in stt_data:
        stt_data["custom_terms"] = _default_custom_terms()
        changed = True

    if "custom_vocabulary_enabled" not in stt_data:
        normalized_custom_terms = _parse_custom_terms(stt_data.get("custom_terms"))
        stt_data["custom_vocabulary_enabled"] = any(
            bool(terms) for terms in normalized_custom_terms.values()
        )
        changed = True

    raw_provider_data = data.get("provider")
    provider_data: dict[str, Any] | None
    if raw_provider_data is None:
        provider_data = {}
        data["provider"] = provider_data
        changed = True
    elif not isinstance(raw_provider_data, dict):
        provider_data = {
            "stt": STTProviderName.DEEPGRAM.value,
            "llm": LLMProviderName.GEMINI.value,
        }
        data["provider"] = provider_data
        changed = True
    else:
        provider_data = raw_provider_data

    if isinstance(provider_data, dict) and "stt" in provider_data:
        raw_stt_provider = provider_data.get("stt")
        normalized_stt_provider = _parse_stt_provider(str(raw_stt_provider)).value
        if raw_stt_provider != normalized_stt_provider:
            provider_data["stt"] = normalized_stt_provider
            changed = True
    if isinstance(provider_data, dict) and "peer_stt" not in provider_data:
        provider_data["peer_stt"] = STTProviderName.DEEPGRAM.value
        changed = True
    if isinstance(provider_data, dict) and "peer_stt" in provider_data:
        raw_peer_provider = provider_data.get("peer_stt")
        normalized_peer_provider = _parse_stt_provider(str(raw_peer_provider)).value
        if raw_peer_provider != normalized_peer_provider:
            provider_data["peer_stt"] = normalized_peer_provider
            changed = True

    if "peer_deepgram_stt" in data:
        del data["peer_deepgram_stt"]
        changed = True

    for key, default_block in peer_block_defaults.items():
        if _normalize_peer_block(data, key, default_block):
            changed = True

    # Keep schema at v2 but backfill Soniox legacy default model upgrade.
    soniox_data = data.get("soniox_stt")
    if isinstance(soniox_data, dict):
        model = soniox_data.get("model")
        # Preserve explicit custom model values and only upgrade legacy default v3.
        if isinstance(model, str) and model.strip() == "stt-rt-v3":
            soniox_data["model"] = "stt-rt-v4"
            changed = True

    gemini_data = data.get("gemini")
    if not isinstance(gemini_data, dict):
        gemini_data = {}
        data["gemini"] = gemini_data
        changed = True

    raw_gemini_model = gemini_data.get("llm_model")
    normalized_gemini_model = _parse_gemini_llm_model(raw_gemini_model).value
    if raw_gemini_model != normalized_gemini_model:
        gemini_data["llm_model"] = normalized_gemini_model
        changed = True

    openrouter_data = data.get("openrouter")
    if not isinstance(openrouter_data, dict):
        openrouter_data = {}
        data["openrouter"] = openrouter_data
        changed = True

    raw_openrouter_model = openrouter_data.get("llm_model")
    normalized_openrouter_model = _parse_openrouter_llm_model(raw_openrouter_model).value
    if raw_openrouter_model != normalized_openrouter_model:
        openrouter_data["llm_model"] = normalized_openrouter_model
        changed = True

    raw_openrouter_routing_mode = openrouter_data.get("routing_mode")
    normalized_openrouter_routing_mode = _parse_openrouter_routing_mode(
        raw_openrouter_routing_mode
    ).value
    if raw_openrouter_routing_mode != normalized_openrouter_routing_mode:
        openrouter_data["routing_mode"] = normalized_openrouter_routing_mode
        changed = True

    raw_openrouter_selected_source = _get_raw_openrouter_selected_source(openrouter_data)
    normalized_openrouter_selected_source = _parse_openrouter_credential_source(
        raw_openrouter_selected_source,
        fallback=_default_openrouter_credential_source_value(data),
    )
    if openrouter_data.get("selected_source") != normalized_openrouter_selected_source.value:
        openrouter_data["selected_source"] = normalized_openrouter_selected_source.value
        changed = True
    if "credential_source" in openrouter_data:
        del openrouter_data["credential_source"]
        changed = True
    if "selected_credential_source" in openrouter_data:
        del openrouter_data["selected_credential_source"]
        changed = True

    raw_openrouter_broker_base_url = openrouter_data.get("broker_base_url")
    normalized_openrouter_broker_base_url = _parse_openrouter_broker_base_url(
        raw_openrouter_broker_base_url
    )
    if raw_openrouter_broker_base_url != normalized_openrouter_broker_base_url:
        openrouter_data["broker_base_url"] = normalized_openrouter_broker_base_url
        changed = True

    qwen_data = data.get("qwen")
    if not isinstance(qwen_data, dict):
        qwen_data = {}
        data["qwen"] = qwen_data
        changed = True

    qwen_asr_data = data.get("qwen_asr_stt")
    qwen_asr_endpoint = qwen_asr_data.get("endpoint") if isinstance(qwen_asr_data, dict) else None

    raw_qwen_region = qwen_data.get("region")
    normalized_qwen_region = _parse_qwen_region(
        raw_qwen_region,
        legacy_asr_endpoint=qwen_asr_endpoint,
    ).value
    if raw_qwen_region != normalized_qwen_region:
        qwen_data["region"] = normalized_qwen_region
        changed = True

    raw_qwen_model = qwen_data.get("llm_model")
    normalized_qwen_model = _parse_qwen_llm_model(raw_qwen_model).value
    if raw_qwen_model != normalized_qwen_model:
        qwen_data["llm_model"] = normalized_qwen_model
        changed = True

    ui_data = data.get("ui")
    if not isinstance(ui_data, dict):
        ui_data = {}
        data["ui"] = ui_data
        changed = True

    if "show_overlay_translation" not in ui_data:
        ui_data["show_overlay_translation"] = True
        changed = True

    if "show_overlay_peer_original" not in ui_data:
        ui_data["show_overlay_peer_original"] = True
        changed = True

    if "overlay_enabled" in ui_data:
        del ui_data["overlay_enabled"]
        changed = True

    managed_identity_data = data.get("managed_identity")
    if not isinstance(managed_identity_data, dict):
        managed_identity_data = {}
        data["managed_identity"] = managed_identity_data
        changed = True

    raw_installation_id = managed_identity_data.get("installation_id")
    normalized_installation_id = (
        raw_installation_id.strip() if isinstance(raw_installation_id, str) else ""
    )
    if raw_installation_id != normalized_installation_id:
        managed_identity_data["installation_id"] = normalized_installation_id
        changed = True

    raw_release_token = managed_identity_data.get("release_token")
    normalized_release_token = _parse_optional_str(raw_release_token)
    if raw_release_token != normalized_release_token:
        managed_identity_data["release_token"] = normalized_release_token
        changed = True

    raw_release_token_expires_at = managed_identity_data.get("release_token_expires_at")
    normalized_release_token_expires_at = _parse_optional_str(raw_release_token_expires_at)
    if raw_release_token_expires_at != normalized_release_token_expires_at:
        managed_identity_data["release_token_expires_at"] = normalized_release_token_expires_at
        changed = True

    raw_verified_hardware_hash = managed_identity_data.get("verified_hardware_hash")
    normalized_verified_hardware_hash = _parse_optional_str(raw_verified_hardware_hash)
    if (
        "verified_hardware_hash" not in managed_identity_data
        or raw_verified_hardware_hash != normalized_verified_hardware_hash
    ):
        managed_identity_data["verified_hardware_hash"] = normalized_verified_hardware_hash
        changed = True

    raw_verified_hardware_hash_salt_version = managed_identity_data.get(
        "verified_hardware_hash_salt_version"
    )
    normalized_verified_hardware_hash_salt_version = _parse_optional_int(
        raw_verified_hardware_hash_salt_version
    )
    if (
        "verified_hardware_hash_salt_version" not in managed_identity_data
        or raw_verified_hardware_hash_salt_version != normalized_verified_hardware_hash_salt_version
    ):
        managed_identity_data["verified_hardware_hash_salt_version"] = (
            normalized_verified_hardware_hash_salt_version
        )
        changed = True

    if data.get("settings_version") != version:
        data["settings_version"] = version
        changed = True

    return data, changed


def from_dict(data: dict[str, Any]) -> AppSettings:
    audio_data = data.get("audio") or {}
    desktop_audio_data = data.get("desktop_audio") or {}
    overlay_calibration_data = data.get("overlay_calibration") or {}
    stt_data = data.get("stt") or {}
    ui_data = data.get("ui") or {}
    managed_identity_data = (
        data.get("managed_identity") if isinstance(data.get("managed_identity"), dict) else {}
    )
    peer_qwen_raw = (
        data.get("peer_qwen_asr_stt") if isinstance(data.get("peer_qwen_asr_stt"), dict) else {}
    )
    peer_soniox_data = (
        data.get("peer_soniox_stt") if isinstance(data.get("peer_soniox_stt"), dict) else {}
    )
    raw_provider_data = data.get("provider")
    provider_data = raw_provider_data if isinstance(raw_provider_data, dict) else {}
    if raw_provider_data is None:
        stt_provider_value = STTProviderName.LOCAL_QWEN.value
    elif isinstance(raw_provider_data, dict):
        stt_provider_value = provider_data.get("stt", STTProviderName.LOCAL_QWEN.value)
    else:
        stt_provider_value = STTProviderName.DEEPGRAM.value
    raw_peer_provider = (
        provider_data.get("peer_stt", STTProviderName.DEEPGRAM.value)
        if isinstance(raw_provider_data, dict)
        else STTProviderName.DEEPGRAM.value
    )

    input_host_api_raw = audio_data.get("input_host_api")
    input_device_raw = audio_data.get("input_device")
    vad_threshold_raw = stt_data.get("vad_speech_threshold")
    legacy_system_prompt = str(data.get("system_prompt", ""))
    system_prompts = _parse_system_prompts(data.get("system_prompts"))
    parsed_custom_terms = _parse_custom_terms(stt_data.get("custom_terms", _default_custom_terms()))
    if "custom_vocabulary_enabled" in stt_data:
        custom_vocabulary_enabled = bool(stt_data.get("custom_vocabulary_enabled"))
    else:
        custom_vocabulary_enabled = any(bool(terms) for terms in parsed_custom_terms.values())

    qwen_raw = data.get("qwen") if isinstance(data.get("qwen"), dict) else {}
    qwen_asr_raw = data.get("qwen_asr_stt") if isinstance(data.get("qwen_asr_stt"), dict) else {}
    openrouter_raw = data.get("openrouter") if isinstance(data.get("openrouter"), dict) else {}
    qwen_settings = QwenSettings(
        region=_parse_qwen_region(
            qwen_raw.get("region"),
            legacy_asr_endpoint=qwen_asr_raw.get("endpoint"),
        ),
        llm_model=_parse_qwen_llm_model(qwen_raw.get("llm_model", QwenLLMModel.QWEN_35_PLUS.value)),
    )

    settings = AppSettings(
        settings_version=_coerce_int(data.get("settings_version"), SETTINGS_SCHEMA_VERSION),
        provider=ProviderSettings(
            stt=_parse_stt_provider(str(stt_provider_value)),
            peer_stt=_parse_stt_provider(str(raw_peer_provider)),
            llm=_parse_llm_provider(provider_data.get("llm", LLMProviderName.GEMINI.value)),
        ),
        languages=LanguageSettings(
            source_language=data.get("languages", {}).get("source_language", "ko"),
            target_language=data.get("languages", {}).get("target_language", "en"),
            peer_source_language=str(data.get("languages", {}).get("peer_source_language", "")),
            peer_target_language=str(data.get("languages", {}).get("peer_target_language", "")),
            recent_source_languages=list(
                dict.fromkeys(
                    list(data.get("languages", {}).get("recent_source_languages") or [])
                    + ["ko", "en", "zh-CN", "ja", "es", "fr"]
                )
            )[:6],
            recent_target_languages=list(
                dict.fromkeys(
                    list(data.get("languages", {}).get("recent_target_languages") or [])
                    + ["ko", "en", "zh-CN", "ja", "es", "fr"]
                )
            )[:6],
        ),
        audio=AudioSettings(
            internal_sample_rate_hz=int(audio_data.get("internal_sample_rate_hz", 16000)),
            internal_channels=int(audio_data.get("internal_channels", 1)),
            ring_buffer_ms=int(audio_data.get("ring_buffer_ms", 500)),
            input_host_api=str(input_host_api_raw) if input_host_api_raw is not None else "",
            input_device=str(input_device_raw) if input_device_raw is not None else "",
        ),
        desktop_audio=DesktopAudioSettings(
            output_device=(
                str(desktop_audio_data.get("output_device"))
                if desktop_audio_data.get("output_device") is not None
                else ""
            ),
            vad_speech_threshold=float(desktop_audio_data.get("vad_speech_threshold", 0.6)),
            vad_hangover_ms=int(desktop_audio_data.get("vad_hangover_ms", 600)),
            vad_pre_roll_ms=int(desktop_audio_data.get("vad_pre_roll_ms", 500)),
        ),
        overlay_calibration=OverlayCalibration(
            anchor=str(
                overlay_calibration_data.get(
                    "anchor",
                    OverlayCalibration().anchor,
                )
            ),
            offset_x=float(
                overlay_calibration_data.get(
                    "offset_x",
                    OverlayCalibration().offset_x,
                )
            ),
            offset_y=float(
                overlay_calibration_data.get(
                    "offset_y",
                    OverlayCalibration().offset_y,
                )
            ),
            distance=float(
                overlay_calibration_data.get(
                    "distance",
                    OverlayCalibration().distance,
                )
            ),
            text_scale=float(
                overlay_calibration_data.get(
                    "text_scale",
                    OverlayCalibration().text_scale,
                )
            ),
            background_alpha=float(
                overlay_calibration_data.get(
                    "background_alpha",
                    OverlayCalibration().background_alpha,
                )
            ),
        ),
        stt=STTSettings(
            drain_timeout_s=float(stt_data.get("drain_timeout_s", 2.0)),
            vad_speech_threshold=float(vad_threshold_raw) if vad_threshold_raw is not None else 0.5,
            low_latency_mode=bool(stt_data.get("low_latency_mode", False)),
            low_latency_vad_hangover_ms=int(stt_data.get("low_latency_vad_hangover_ms", 600)),
            low_latency_merge_gap_ms=int(stt_data.get("low_latency_merge_gap_ms", 600)),
            low_latency_spec_retry_max=int(stt_data.get("low_latency_spec_retry_max", 10)),
            custom_vocabulary_enabled=custom_vocabulary_enabled,
            custom_terms=parsed_custom_terms,
        ),
        deepgram_stt=DeepgramSTTSettings(
            model=str(data.get("deepgram_stt", {}).get("model", "nova-3")),
        ),
        qwen_asr_stt=QwenASRSTTSettings(
            model=str(data.get("qwen_asr_stt", {}).get("model", "qwen3-asr-flash-realtime")),
            endpoint=qwen_settings.get_asr_endpoint(),
        ),
        soniox_stt=SonioxSTTSettings(
            model=str(data.get("soniox_stt", {}).get("model", "stt-rt-v4")),
            endpoint=str(
                data.get("soniox_stt", {}).get(
                    "endpoint", "wss://stt-rt.soniox.com/transcribe-websocket"
                )
            ),
            keepalive_interval_s=float(
                data.get("soniox_stt", {}).get("keepalive_interval_s", 10.0)
            ),
            trailing_silence_ms=int(data.get("soniox_stt", {}).get("trailing_silence_ms", 100)),
        ),
        peer_qwen_asr_stt=PeerQwenASRSTTSettings(
            model=_parse_optional_str(peer_qwen_raw.get("model")),
            region=(
                QwenRegion(peer_qwen_raw["region"])
                if peer_qwen_raw.get("region") in {region.value for region in QwenRegion}
                else None
            ),
        ),
        peer_soniox_stt=PeerSonioxSTTSettings(
            model=_parse_optional_str(peer_soniox_data.get("model")),
            endpoint=_parse_optional_str(peer_soniox_data.get("endpoint")),
            keepalive_interval_s=_parse_optional_float(
                peer_soniox_data.get("keepalive_interval_s")
            ),
            trailing_silence_ms=_parse_optional_int(peer_soniox_data.get("trailing_silence_ms")),
        ),
        gemini=GeminiSettings(
            llm_model=_parse_gemini_llm_model(
                data.get("gemini", {}).get("llm_model", GeminiLLMModel.GEMINI_31_FLASH_LITE.value)
            ),
        ),
        openrouter=OpenRouterSettings(
            llm_model=_parse_openrouter_llm_model(
                openrouter_raw.get(
                    "llm_model",
                    OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value,
                )
            ),
            routing_mode=_parse_openrouter_routing_mode(
                openrouter_raw.get(
                    "routing_mode",
                    OpenRouterRoutingMode.LATENCY.value,
                )
            ),
            selected_source=_parse_openrouter_credential_source(
                _get_raw_openrouter_selected_source(openrouter_raw),
                fallback=_default_openrouter_credential_source_value(data),
            ),
            broker_base_url=_parse_openrouter_broker_base_url(
                openrouter_raw.get("broker_base_url")
            ),
        ),
        qwen=qwen_settings,
        llm=LLMSettings(concurrency_limit=int(data.get("llm", {}).get("concurrency_limit", 5))),
        osc=OSCSettings(
            host=str(data.get("osc", {}).get("host", "127.0.0.1")),
            port=int(data.get("osc", {}).get("port", 9000)),
            chatbox_address=str(data.get("osc", {}).get("chatbox_address", "/chatbox/input")),
            chatbox_send=bool(data.get("osc", {}).get("chatbox_send", True)),
            chatbox_clear=bool(data.get("osc", {}).get("chatbox_clear", False)),
            chatbox_max_chars=int(data.get("osc", {}).get("chatbox_max_chars", 144)),
            cooldown_s=float(data.get("osc", {}).get("cooldown_s", 1.5)),
            ttl_s=float(data.get("osc", {}).get("ttl_s", 7.0)),
            vrc_mic_intercept=bool(data.get("osc", {}).get("vrc_mic_intercept", False)),
            chatbox_include_source=bool(data.get("osc", {}).get("chatbox_include_source", True)),
        ),
        secrets=SecretsSettings(
            backend=SecretsBackend(
                data.get("secrets", {}).get("backend", SecretsBackend.KEYRING.value)
            ),
            encrypted_file_path=data.get("secrets", {}).get("encrypted_file_path", "secrets.json"),
        ),
        ui=UiSettings(
            locale=str(ui_data.get("locale", "en")),
            show_overlay_translation=bool(ui_data.get("show_overlay_translation", True)),
            show_overlay_peer_original=bool(ui_data.get("show_overlay_peer_original", True)),
            peer_translation_enabled=bool(ui_data.get("peer_translation_enabled", False)),
            integrated_context_enabled=bool(ui_data.get("integrated_context_enabled", False)),
            integrated_context_bootstrapped=bool(
                ui_data.get("integrated_context_bootstrapped", False)
            ),
        ),
        api_key_verified=ApiKeyVerificationSettings(
            deepgram=bool(data.get("api_key_verified", {}).get("deepgram", False)),
            soniox=bool(data.get("api_key_verified", {}).get("soniox", False)),
            google=bool(data.get("api_key_verified", {}).get("google", False)),
            openrouter=bool(data.get("api_key_verified", {}).get("openrouter", False)),
            alibaba_beijing=bool(data.get("api_key_verified", {}).get("alibaba_beijing", False)),
            alibaba_singapore=bool(
                data.get("api_key_verified", {}).get("alibaba_singapore", False)
            ),
        ),
        managed_identity=ManagedIdentitySettings(
            installation_id=_parse_optional_str(managed_identity_data.get("installation_id")) or "",
            release_token=_parse_optional_str(managed_identity_data.get("release_token")),
            release_token_expires_at=_parse_optional_str(
                managed_identity_data.get("release_token_expires_at")
            ),
            verified_hardware_hash=_parse_optional_str(
                managed_identity_data.get("verified_hardware_hash")
            ),
            verified_hardware_hash_salt_version=_parse_optional_int(
                managed_identity_data.get("verified_hardware_hash_salt_version")
            ),
        ),
        system_prompt=legacy_system_prompt,
        system_prompts=system_prompts,
    )

    selected_prompt_key = _llm_prompt_key(settings.provider.llm)
    if legacy_system_prompt and selected_prompt_key not in settings.system_prompts:
        settings.system_prompts[selected_prompt_key] = legacy_system_prompt

    if settings.system_prompts.get("qwen", "").strip() == LEGACY_QWEN_DEFAULT_PROMPT:
        from puripuly_heart.config.prompts import load_prompt_for_provider

        settings.system_prompts["qwen"] = load_prompt_for_provider("qwen")

    selected_prompt = settings.system_prompts.get(selected_prompt_key, "").strip()
    if selected_prompt:
        settings.system_prompt = selected_prompt
    settings.validate()
    return settings


def load_settings(path: Path) -> AppSettings:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("settings file must contain a JSON object")
    migrated, changed = _migrate_settings_dict(raw)
    settings = from_dict(migrated)
    if changed:
        save_settings(path, settings)
    return settings


def save_settings(path: Path, settings: AppSettings) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        path,
        json.dumps(to_dict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
