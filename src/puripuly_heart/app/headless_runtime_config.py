from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from puripuly_heart.config.paths import VNEXT_APP_DIR_NAME
from puripuly_heart.config.resolved import ResolvedLLMConfig, ResolvedSTTConfig
from puripuly_heart.config.runtime_resolution import (
    DEEPSEEK_MODEL_V4_FLASH,
    DEEPSEEK_MODEL_V4_PRO,
    GEMINI_MODEL_3_FLASH,
    GEMINI_MODEL_31_FLASH_LITE,
    OPENROUTER_MODEL_DEEPSEEK_V4_FLASH,
    OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT,
    OPENROUTER_MODEL_QWEN_35_FLASH_02_23,
    OPENROUTER_SOURCE_BYOK,
    OPENROUTER_SOURCE_MANAGED,
    OPENROUTER_SOURCE_NONE,
    STT_DEFAULT_SAMPLE_RATE_HZ,
    STT_DEFAULT_VAD_PRE_ROLL_MS,
    TRANSLATION_CONNECTION_MANAGED,
    TRANSLATION_CONNECTION_MANAGED_CHINA,
    TRANSLATION_CONNECTION_OPENROUTER,
    TRANSLATION_MODEL_DEEPSEEK_V4_FLASH,
    TRANSLATION_MODEL_DEEPSEEK_V4_PRO,
    TRANSLATION_MODEL_GEMINI_3_FLASH,
    TRANSLATION_MODEL_GEMINI_31_FLASH_LITE,
    TRANSLATION_MODEL_GEMMA4,
    TRANSLATION_MODEL_OPENROUTER_QWEN_35_FLASH,
    TRANSLATION_MODEL_QWEN_35_PLUS,
    DirectProviderRuntimeIntent,
    OpenRouterRuntimeIntent,
    RuntimeResolutionInput,
    STTRuntimeIntent,
    TranslationRuntimeIntent,
    normalize_openrouter_runtime_intent,
    normalize_translation_runtime_intent,
    resolve_llm_config,
    resolve_stt_config,
)
from puripuly_heart.config.settings import MAX_CUSTOM_VOCAB_TERMS
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.runtime.peer_channel import PeerRuntimeConfig
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
    SecretStore,
)

SECRETS_PASSPHRASE_ENV: Final = "PURIPULY_HEART_SECRETS_PASSPHRASE"


@dataclass(frozen=True, slots=True)
class HeadlessOSCRuntimeConfig:
    host: str
    port: int
    chatbox_address: str
    chatbox_send: bool
    chatbox_clear: bool
    chatbox_max_chars: int
    chatbox_include_source: bool
    vrc_mic_intercept: bool


@dataclass(frozen=True, slots=True)
class HeadlessLanguageRuntimeConfig:
    source_language: str
    target_language: str
    peer_source_language: str


@dataclass(frozen=True, slots=True)
class HeadlessAudioRuntimeConfig:
    input_host_api: str
    input_device: str
    ring_buffer_ms: int
    internal_sample_rate_hz: int
    internal_channels: int


@dataclass(frozen=True, slots=True)
class HeadlessDesktopAudioRuntimeConfig:
    output_device: str
    vad_speech_threshold: float
    vad_hangover_ms: int
    vad_pre_roll_ms: int


@dataclass(frozen=True, slots=True)
class HeadlessSTTRuntimeConfig:
    provider: str
    drain_timeout_s: float
    low_latency_mode: bool
    low_latency_merge_gap_ms: int
    low_latency_spec_retry_max: int
    low_latency_vad_hangover_ms: int
    vad_speech_threshold: float
    custom_vocabulary_enabled: bool
    custom_terms: Mapping[str, tuple[str, ...]]
    deepgram_model: str
    qwen_asr_model: str
    qwen_region: str
    soniox_model: str
    soniox_endpoint: str
    soniox_keepalive_interval_s: float
    soniox_trailing_silence_ms: int


@dataclass(frozen=True, slots=True)
class HeadlessPeerSTTRuntimeConfig:
    provider: str
    source_language: str


@dataclass(frozen=True, slots=True)
class HeadlessUIRuntimeConfig:
    peer_translation_enabled: bool
    integrated_context_enabled: bool


@dataclass(frozen=True, slots=True)
class HeadlessMicRuntimeConfig:
    config_path: Path
    vad_model_path: Path
    use_llm: bool
    secrets: SecretStore = field(repr=False)
    osc: HeadlessOSCRuntimeConfig
    languages: HeadlessLanguageRuntimeConfig
    system_prompt: str
    audio: HeadlessAudioRuntimeConfig
    stt: HeadlessSTTRuntimeConfig
    peer_stt: HeadlessPeerSTTRuntimeConfig
    desktop_audio: HeadlessDesktopAudioRuntimeConfig
    ui: HeadlessUIRuntimeConfig
    llm_config: ResolvedLLMConfig | None
    stt_config: ResolvedSTTConfig
    peer_stt_config: ResolvedSTTConfig
    peer_runtime_config: PeerRuntimeConfig


@dataclass(frozen=True, slots=True)
class HeadlessStdinRuntimeConfig:
    osc: HeadlessOSCRuntimeConfig
    languages: HeadlessLanguageRuntimeConfig
    system_prompt: str
    chatbox_include_source: bool


def _osc_config_from_vnext(settings: AppSettingsVNext) -> HeadlessOSCRuntimeConfig:
    osc = settings.intent.osc
    return HeadlessOSCRuntimeConfig(
        host=osc.host,
        port=osc.port,
        chatbox_address=osc.chatbox_address,
        chatbox_send=osc.chatbox_send,
        chatbox_clear=osc.chatbox_clear,
        chatbox_max_chars=osc.chatbox_max_chars,
        chatbox_include_source=osc.chatbox_include_source,
        vrc_mic_intercept=osc.vrc_mic_intercept,
    )


def _language_config_from_vnext(
    settings: AppSettingsVNext,
) -> HeadlessLanguageRuntimeConfig:
    languages = settings.intent.languages
    return HeadlessLanguageRuntimeConfig(
        source_language=languages.source_language,
        target_language=languages.target_language,
        peer_source_language=languages.peer_source_language,
    )


def _audio_config_from_vnext(settings: AppSettingsVNext) -> HeadlessAudioRuntimeConfig:
    audio = settings.intent.audio
    return HeadlessAudioRuntimeConfig(
        input_host_api=audio.input_host_api,
        input_device=audio.input_device,
        ring_buffer_ms=audio.ring_buffer_ms,
        internal_sample_rate_hz=STT_DEFAULT_SAMPLE_RATE_HZ,
        internal_channels=1,
    )


def _desktop_audio_config_from_vnext(
    settings: AppSettingsVNext,
) -> HeadlessDesktopAudioRuntimeConfig:
    desktop = settings.intent.desktop_audio
    return HeadlessDesktopAudioRuntimeConfig(
        output_device=desktop.output_device,
        vad_speech_threshold=desktop.vad_speech_threshold,
        vad_hangover_ms=desktop.vad_hangover_ms,
        vad_pre_roll_ms=desktop.vad_pre_roll_ms,
    )


def _custom_terms_from_vnext(
    custom_terms: Mapping[str, list[str]],
) -> Mapping[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for language, terms_list in custom_terms.items():
        seen_terms: set[str] = set()
        effective_terms: list[str] = []
        for term in terms_list:
            normalized_term = term.strip()
            if not normalized_term or normalized_term in seen_terms:
                continue
            if len(effective_terms) >= MAX_CUSTOM_VOCAB_TERMS:
                break
            seen_terms.add(normalized_term)
            effective_terms.append(normalized_term)
        # Preserve exact-empty semantics: if the language key exists, keep it
        # (even as an empty tuple) so base-language fallback does not occur.
        normalized[language] = tuple(effective_terms)
    return normalized


def _stt_config_from_vnext(settings: AppSettingsVNext) -> HeadlessSTTRuntimeConfig:
    stt = settings.intent.stt
    terms: Mapping[str, tuple[str, ...]] = (
        _custom_terms_from_vnext(stt.custom_terms) if stt.custom_vocabulary_enabled else {}
    )
    return HeadlessSTTRuntimeConfig(
        provider=stt.provider,
        drain_timeout_s=stt.drain_timeout_s,
        low_latency_mode=stt.low_latency_mode,
        low_latency_merge_gap_ms=stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=stt.low_latency_spec_retry_max,
        low_latency_vad_hangover_ms=stt.low_latency_vad_hangover_ms,
        vad_speech_threshold=stt.vad_speech_threshold,
        custom_vocabulary_enabled=stt.custom_vocabulary_enabled,
        custom_terms=terms,
        deepgram_model=stt.deepgram.model,
        qwen_asr_model=stt.qwen_asr.model,
        qwen_region=settings.intent.translation.qwen.region,
        soniox_model=stt.soniox.model,
        soniox_endpoint=stt.soniox.endpoint,
        soniox_keepalive_interval_s=stt.soniox.keepalive_interval_s,
        soniox_trailing_silence_ms=stt.soniox.trailing_silence_ms,
    )


def _peer_stt_config_from_vnext(
    settings: AppSettingsVNext,
) -> HeadlessPeerSTTRuntimeConfig:
    return HeadlessPeerSTTRuntimeConfig(
        provider=settings.intent.peer_stt.provider,
        source_language=settings.intent.languages.peer_source_language,
    )


def _ui_config_from_vnext(settings: AppSettingsVNext) -> HeadlessUIRuntimeConfig:
    # Headless peer translation is intentionally disabled in 11e; there is no
    # headless runtime activation intent yet. EULA acceptance is consent state,
    # not an active flag.
    return HeadlessUIRuntimeConfig(
        peer_translation_enabled=False,
        integrated_context_enabled=settings.intent.integrated_context.enabled,
    )


def _translation_intent_from_vnext(
    settings: AppSettingsVNext,
) -> TranslationRuntimeIntent:
    translation = settings.intent.translation
    return normalize_translation_runtime_intent(
        model=translation.model,
        connection=translation.connection,
        concurrency_limit=translation.concurrency_limit,
    )


def _openrouter_model_for_translation(model: str) -> str:
    if model == TRANSLATION_MODEL_GEMMA4:
        return OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT
    if model == TRANSLATION_MODEL_DEEPSEEK_V4_FLASH:
        return OPENROUTER_MODEL_DEEPSEEK_V4_FLASH
    if model == TRANSLATION_MODEL_OPENROUTER_QWEN_35_FLASH:
        return OPENROUTER_MODEL_QWEN_35_FLASH_02_23
    return OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT


def _openrouter_source_for_translation_connection(connection: str) -> str:
    if connection in (TRANSLATION_CONNECTION_MANAGED, TRANSLATION_CONNECTION_MANAGED_CHINA):
        return OPENROUTER_SOURCE_MANAGED
    if connection == TRANSLATION_CONNECTION_OPENROUTER:
        return OPENROUTER_SOURCE_BYOK
    return OPENROUTER_SOURCE_NONE


def _openrouter_intent_from_vnext(
    settings: AppSettingsVNext,
) -> OpenRouterRuntimeIntent:
    translation = settings.intent.translation
    return normalize_openrouter_runtime_intent(
        model=_openrouter_model_for_translation(translation.model),
        selected_source=_openrouter_source_for_translation_connection(translation.connection),
        fallback_selection_alias=translation.openrouter_fallback_selection_alias,
        routing_mode=translation.openrouter_routing_mode,
        provider_routing="default",
        broker_base_url=translation.openrouter_broker_base_url,
    )


def _gemini_model_for_translation(model: str) -> str:
    if model == TRANSLATION_MODEL_GEMINI_3_FLASH:
        return GEMINI_MODEL_3_FLASH
    if model == TRANSLATION_MODEL_GEMINI_31_FLASH_LITE:
        return GEMINI_MODEL_31_FLASH_LITE
    return GEMINI_MODEL_31_FLASH_LITE


def _deepseek_flash_model_for_translation(model: str) -> str | None:
    if model == TRANSLATION_MODEL_DEEPSEEK_V4_FLASH:
        return DEEPSEEK_MODEL_V4_FLASH
    return None


def _deepseek_pro_model_for_translation(model: str) -> str | None:
    if model == TRANSLATION_MODEL_DEEPSEEK_V4_PRO:
        return DEEPSEEK_MODEL_V4_PRO
    return None


def _direct_intent_from_vnext(
    settings: AppSettingsVNext,
) -> DirectProviderRuntimeIntent:
    translation = settings.intent.translation
    local = settings.intent.local_llm
    qwen = translation.qwen
    translation_model = translation.model

    gemini_model = _gemini_model_for_translation(translation_model)
    deepseek_flash_model = _deepseek_flash_model_for_translation(translation_model)
    deepseek_pro_model = _deepseek_pro_model_for_translation(translation_model)
    qwen_model = qwen.llm_model if translation_model == TRANSLATION_MODEL_QWEN_35_PLUS else None

    return DirectProviderRuntimeIntent(
        gemini_3_flash_model=gemini_model,
        gemini_31_flash_lite_model=gemini_model,
        deepseek_v4_flash_model=deepseek_flash_model,
        deepseek_v4_pro_model=deepseek_pro_model,
        qwen_35_plus_model=qwen_model,
        qwen_region=qwen.region,
        local_llm_backend=local.backend,
        local_llm_base_url=local.base_url,
        local_llm_model=local.model,
        local_llm_extra_body=local.extra_body,
    )


def _runtime_resolution_input_from_vnext(
    settings: AppSettingsVNext,
) -> RuntimeResolutionInput:
    return RuntimeResolutionInput(
        translation=_translation_intent_from_vnext(settings),
        openrouter=_openrouter_intent_from_vnext(settings),
        direct=_direct_intent_from_vnext(settings),
    )


def _self_stt_runtime_intent_from_vnext(
    settings: AppSettingsVNext,
) -> STTRuntimeIntent:
    stt = settings.intent.stt
    audio = settings.intent.audio
    languages = settings.intent.languages
    terms: Mapping[str, tuple[str, ...]] = (
        _custom_terms_from_vnext(stt.custom_terms) if stt.custom_vocabulary_enabled else {}
    )
    return STTRuntimeIntent(
        channel="self",
        provider=stt.provider,
        source_language=languages.source_language,
        input_host_api=audio.input_host_api,
        input_device=audio.input_device,
        output_device=None,
        sample_rate_hz=STT_DEFAULT_SAMPLE_RATE_HZ,
        channels=1,
        ring_buffer_ms=audio.ring_buffer_ms,
        drain_timeout_s=stt.drain_timeout_s,
        vad_speech_threshold=stt.vad_speech_threshold,
        vad_hangover_ms=stt.low_latency_vad_hangover_ms,
        vad_pre_roll_ms=STT_DEFAULT_VAD_PRE_ROLL_MS,
        low_latency_enabled=stt.low_latency_mode,
        low_latency_merge_gap_ms=stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=stt.low_latency_spec_retry_max,
        custom_vocabulary_enabled=stt.custom_vocabulary_enabled,
        custom_terms=terms,
        deepgram_model=stt.deepgram.model,
        qwen_asr_model=stt.qwen_asr.model,
        qwen_region=settings.intent.translation.qwen.region,
        soniox_model=stt.soniox.model,
        soniox_endpoint=stt.soniox.endpoint,
        soniox_keepalive_interval_s=stt.soniox.keepalive_interval_s,
        soniox_trailing_silence_ms=stt.soniox.trailing_silence_ms,
    )


def _peer_stt_runtime_intent_from_vnext(
    settings: AppSettingsVNext,
) -> STTRuntimeIntent:
    desktop = settings.intent.desktop_audio
    stt = settings.intent.stt
    return STTRuntimeIntent(
        channel="peer",
        provider=settings.intent.peer_stt.provider,
        source_language=settings.intent.languages.peer_source_language,
        input_host_api=None,
        input_device=None,
        output_device=desktop.output_device,
        sample_rate_hz=STT_DEFAULT_SAMPLE_RATE_HZ,
        channels=1,
        ring_buffer_ms=settings.intent.audio.ring_buffer_ms,
        drain_timeout_s=stt.drain_timeout_s,
        vad_speech_threshold=desktop.vad_speech_threshold,
        vad_hangover_ms=desktop.vad_hangover_ms,
        vad_pre_roll_ms=desktop.vad_pre_roll_ms,
        low_latency_enabled=stt.low_latency_mode,
        low_latency_merge_gap_ms=stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=stt.low_latency_spec_retry_max,
        custom_vocabulary_enabled=False,
        custom_terms={},
        deepgram_model=stt.deepgram.model,
        qwen_asr_model=stt.qwen_asr.model,
        qwen_region=settings.intent.translation.qwen.region,
        soniox_model=stt.soniox.model,
        soniox_endpoint=stt.soniox.endpoint,
        soniox_keepalive_interval_s=stt.soniox.keepalive_interval_s,
        soniox_trailing_silence_ms=stt.soniox.trailing_silence_ms,
    )


def _resolved_stt_keyterms(config: ResolvedSTTConfig) -> tuple[str, ...]:
    if not config.custom_vocabulary_enabled:
        return ()
    exact_terms = config.custom_terms.get(config.source_language)
    if exact_terms is not None:
        return tuple(exact_terms)
    base_language = config.source_language.split("-")[0].lower()
    return tuple(config.custom_terms.get(base_language, ()))


def _peer_provider_signature(
    peer_stt_config: ResolvedSTTConfig,
) -> tuple[object, ...]:
    provider_options = peer_stt_config.provider_options
    keepalive: object = None
    trailing_silence: object = None
    if isinstance(provider_options, Mapping):
        keepalive = provider_options.get("keepalive_interval_s")
        trailing_silence = provider_options.get("trailing_silence_ms")
    return (
        peer_stt_config.provider,
        peer_stt_config.source_language,
        peer_stt_config.sample_rate_hz,
        peer_stt_config.model,
        peer_stt_config.region,
        peer_stt_config.endpoint,
        keepalive,
        trailing_silence,
        _resolved_stt_keyterms(peer_stt_config),
    )


def _peer_runtime_config_from_vnext(
    settings: AppSettingsVNext,
    peer_stt_config: ResolvedSTTConfig,
) -> PeerRuntimeConfig:
    desktop = settings.intent.desktop_audio
    provider_signature = _peer_provider_signature(peer_stt_config)
    return PeerRuntimeConfig(
        backend=peer_stt_config,
        output_device=desktop.output_device,
        vad_threshold=desktop.vad_speech_threshold,
        vad_hangover_ms=desktop.vad_hangover_ms,
        vad_pre_roll_ms=desktop.vad_pre_roll_ms,
        provider_signature=provider_signature,
        runtime_signature=(
            peer_stt_config.source_language,
            desktop.output_device,
            desktop.vad_speech_threshold,
            desktop.vad_hangover_ms,
            desktop.vad_pre_roll_ms,
            provider_signature,
        ),
    )


def create_secret_store_from_vnext_intent(
    settings: AppSettingsVNext,
    *,
    config_path: Path,
    passphrase: str | None = None,
) -> SecretStore:
    secrets = settings.intent.secrets
    backend = secrets.backend
    if backend == "keyring":
        return KeyringSecretStore(service_name=VNEXT_APP_DIR_NAME)
    if backend == "encrypted_file":
        passphrase = passphrase or os.getenv(SECRETS_PASSPHRASE_ENV)
        if not passphrase:
            raise ValueError(
                "encrypted_file secrets backend requires a passphrase; "
                f"set {SECRETS_PASSPHRASE_ENV} or pass passphrase explicitly"
            )
        path = Path(secrets.encrypted_file_path)
        if not path.is_absolute():
            path = config_path.parent / path
        return EncryptedFileSecretStore(path=path, passphrase=passphrase)
    raise ValueError(f"Unsupported secrets backend: {backend}")


def resolve_llm_config_from_vnext_settings(
    settings: AppSettingsVNext,
) -> ResolvedLLMConfig:
    return resolve_llm_config(_runtime_resolution_input_from_vnext(settings))


def build_headless_mic_runtime_config(
    settings: AppSettingsVNext,
    *,
    config_path: Path,
    vad_model_path: Path,
    use_llm: bool,
    secret_store: SecretStore,
) -> HeadlessMicRuntimeConfig:
    runtime_input = _runtime_resolution_input_from_vnext(settings)
    llm_config = resolve_llm_config(runtime_input) if use_llm else None
    stt_config = resolve_stt_config(_self_stt_runtime_intent_from_vnext(settings))
    peer_stt_config = resolve_stt_config(_peer_stt_runtime_intent_from_vnext(settings))
    peer_runtime_config = _peer_runtime_config_from_vnext(settings, peer_stt_config)

    return HeadlessMicRuntimeConfig(
        config_path=config_path,
        vad_model_path=vad_model_path,
        use_llm=use_llm,
        secrets=secret_store,
        osc=_osc_config_from_vnext(settings),
        languages=_language_config_from_vnext(settings),
        system_prompt=settings.intent.prompts.system_prompt,
        audio=_audio_config_from_vnext(settings),
        stt=_stt_config_from_vnext(settings),
        peer_stt=_peer_stt_config_from_vnext(settings),
        desktop_audio=_desktop_audio_config_from_vnext(settings),
        ui=_ui_config_from_vnext(settings),
        llm_config=llm_config,
        stt_config=stt_config,
        peer_stt_config=peer_stt_config,
        peer_runtime_config=peer_runtime_config,
    )


def build_headless_stdin_runtime_config(
    settings: AppSettingsVNext,
) -> HeadlessStdinRuntimeConfig:
    osc = _osc_config_from_vnext(settings)
    return HeadlessStdinRuntimeConfig(
        osc=osc,
        languages=_language_config_from_vnext(settings),
        system_prompt=settings.intent.prompts.system_prompt,
        chatbox_include_source=osc.chatbox_include_source,
    )


__all__ = [
    "HeadlessAudioRuntimeConfig",
    "HeadlessDesktopAudioRuntimeConfig",
    "HeadlessLanguageRuntimeConfig",
    "HeadlessMicRuntimeConfig",
    "HeadlessOSCRuntimeConfig",
    "HeadlessPeerSTTRuntimeConfig",
    "HeadlessSTTRuntimeConfig",
    "HeadlessStdinRuntimeConfig",
    "HeadlessUIRuntimeConfig",
    "build_headless_mic_runtime_config",
    "build_headless_stdin_runtime_config",
    "create_secret_store_from_vnext_intent",
    "resolve_llm_config_from_vnext_settings",
]
