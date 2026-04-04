from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from puripuly_heart.ui.overlay_calibration import OverlayCalibration

SETTINGS_SCHEMA_VERSION = 4
MAX_CUSTOM_VOCAB_TERMS = 100
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
    vad_hangover_ms: int = 900
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
    endpoint: str = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

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
class PeerDeepgramSTTSettings:
    model: str | None = None

    def validate(self) -> None:
        if self.model is not None and not self.model:
            raise ValueError("peer deepgram model override must be non-empty")


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
    concurrency_limit: int = 2

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
    alibaba_beijing: bool = False
    alibaba_singapore: bool = False

    def validate(self) -> None:
        pass  # No validation needed


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
    peer_deepgram_stt: PeerDeepgramSTTSettings = field(default_factory=PeerDeepgramSTTSettings)
    peer_qwen_asr_stt: PeerQwenASRSTTSettings = field(default_factory=PeerQwenASRSTTSettings)
    peer_soniox_stt: PeerSonioxSTTSettings = field(default_factory=PeerSonioxSTTSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    qwen: QwenSettings = field(default_factory=QwenSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    osc: OSCSettings = field(default_factory=OSCSettings)
    secrets: SecretsSettings = field(default_factory=SecretsSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    api_key_verified: ApiKeyVerificationSettings = field(default_factory=ApiKeyVerificationSettings)
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
        self.peer_deepgram_stt.validate()
        self.peer_qwen_asr_stt.validate()
        self.peer_soniox_stt.validate()
        self.gemini.validate()
        self.qwen.validate()
        self.llm.validate()
        self.osc.validate()
        self.secrets.validate()
        self.ui.validate()
        self.api_key_verified.validate()
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
            "endpoint": settings.qwen_asr_stt.endpoint,
        },
        "soniox_stt": {
            "model": settings.soniox_stt.model,
            "endpoint": settings.soniox_stt.endpoint,
            "keepalive_interval_s": settings.soniox_stt.keepalive_interval_s,
            "trailing_silence_ms": settings.soniox_stt.trailing_silence_ms,
        },
        "peer_deepgram_stt": {
            "model": settings.peer_deepgram_stt.model,
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
            "alibaba_beijing": settings.api_key_verified.alibaba_beijing,
            "alibaba_singapore": settings.api_key_verified.alibaba_singapore,
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


def _llm_prompt_key(provider: LLMProviderName) -> str:
    return "gemini" if provider == LLMProviderName.GEMINI else "qwen"


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


def _normalize_peer_block(
    data: dict[str, Any], key: str, default_block: dict[str, Any]
) -> bool:
    if isinstance(data.get(key), dict):
        return False
    data[key] = copy.deepcopy(default_block)
    return True


def _migrate_settings_dict(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    data: dict[str, Any] = copy.deepcopy(raw)
    changed = False
    peer_block_defaults: dict[str, dict[str, Any]] = {
        "peer_deepgram_stt": {"model": None},
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

    qwen_data = data.get("qwen")
    if not isinstance(qwen_data, dict):
        qwen_data = {}
        data["qwen"] = qwen_data
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
    peer_deepgram_data = data.get("peer_deepgram_stt") if isinstance(data.get("peer_deepgram_stt"), dict) else {}
    peer_qwen_raw = data.get("peer_qwen_asr_stt") if isinstance(data.get("peer_qwen_asr_stt"), dict) else {}
    peer_soniox_data = data.get("peer_soniox_stt") if isinstance(data.get("peer_soniox_stt"), dict) else {}
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

    settings = AppSettings(
        settings_version=_coerce_int(data.get("settings_version"), SETTINGS_SCHEMA_VERSION),
        provider=ProviderSettings(
            stt=_parse_stt_provider(str(stt_provider_value)),
            peer_stt=_parse_stt_provider(str(raw_peer_provider)),
            llm=LLMProviderName(provider_data.get("llm", LLMProviderName.GEMINI.value)),
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
            vad_hangover_ms=int(desktop_audio_data.get("vad_hangover_ms", 900)),
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
            endpoint=str(
                data.get("qwen_asr_stt", {}).get(
                    "endpoint", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
                )
            ),
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
        peer_deepgram_stt=PeerDeepgramSTTSettings(
            model=_parse_optional_str(peer_deepgram_data.get("model")),
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
            keepalive_interval_s=_parse_optional_float(peer_soniox_data.get("keepalive_interval_s")),
            trailing_silence_ms=_parse_optional_int(peer_soniox_data.get("trailing_silence_ms")),
        ),
        gemini=GeminiSettings(
            llm_model=_parse_gemini_llm_model(
                data.get("gemini", {}).get("llm_model", GeminiLLMModel.GEMINI_31_FLASH_LITE.value)
            ),
        ),
        qwen=QwenSettings(
            region=QwenRegion(data.get("qwen", {}).get("region", QwenRegion.BEIJING.value)),
            llm_model=_parse_qwen_llm_model(
                data.get("qwen", {}).get("llm_model", QwenLLMModel.QWEN_35_PLUS.value)
            ),
        ),
        llm=LLMSettings(concurrency_limit=int(data.get("llm", {}).get("concurrency_limit", 2))),
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
            alibaba_beijing=bool(data.get("api_key_verified", {}).get("alibaba_beijing", False)),
            alibaba_singapore=bool(
                data.get("api_key_verified", {}).get("alibaba_singapore", False)
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
    path.write_text(json.dumps(to_dict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
