from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    QwenRegion,
    SecretsBackend,
    SecretsSettings,
    STTProviderName,
)
from puripuly_heart.core.llm.provider import LLMProvider, SemaphoreLLMProvider
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
    SecretStore,
)
from puripuly_heart.core.stt.backend import STTBackend
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"


@dataclass(frozen=True, slots=True)
class ResolvedPeerSTTConfig:
    provider: STTProviderName
    source_language: str
    sample_rate_hz: int
    keyterms: tuple[str, ...]
    deepgram_model: str | None = None
    qwen_model: str | None = None
    qwen_region: QwenRegion | None = None
    soniox_model: str | None = None
    soniox_endpoint: str | None = None
    soniox_keepalive_interval_s: float | None = None
    soniox_trailing_silence_ms: int | None = None


def create_secret_store(
    settings: SecretsSettings,
    *,
    config_path: Path,
    passphrase: str | None = None,
) -> SecretStore:
    passphrase = passphrase or os.getenv(SECRETS_PASSPHRASE_ENV)

    if settings.backend == SecretsBackend.KEYRING:
        return KeyringSecretStore()

    if settings.backend == SecretsBackend.ENCRYPTED_FILE:
        if not passphrase:
            raise ValueError(
                "encrypted_file secrets backend requires a passphrase; "
                f"set {SECRETS_PASSPHRASE_ENV} or pass passphrase explicitly"
            )
        path = Path(settings.encrypted_file_path)
        if not path.is_absolute():
            path = config_path.parent / path
        return EncryptedFileSecretStore(path=path, passphrase=passphrase)

    raise ValueError(f"Unsupported secrets backend: {settings.backend}")


def _get_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str | None:
    value = secrets.get(key)
    if value:
        return value
    env = os.getenv(env_var)
    if env:
        return env
    return None


def _get_secret_any(
    secrets: SecretStore,
    *,
    key: str,
    env_vars: tuple[str, ...],
    legacy_keys: tuple[str, ...] = (),
) -> str | None:
    value = secrets.get(key)
    if value:
        return value
    for legacy_key in legacy_keys:
        legacy_value = secrets.get(legacy_key)
        if legacy_value:
            # Backfill to the new key so subsequent runs do not rely on fallback.
            with contextlib.suppress(Exception):
                secrets.set(key, legacy_value)
            return legacy_value
    for env_var in env_vars:
        env = os.getenv(env_var)
        if env:
            return env
    return None


def require_secret_any(
    secrets: SecretStore,
    *,
    key: str,
    env_vars: tuple[str, ...],
    legacy_keys: tuple[str, ...] = (),
) -> str:
    value = _get_secret_any(secrets, key=key, env_vars=env_vars, legacy_keys=legacy_keys)
    if value:
        return value
    env_list = ", ".join(env_vars)
    raise ValueError(f"Missing secret `{key}` (or env vars {env_list})")


def require_secret(
    secrets: SecretStore,
    *,
    key: str,
    env_var: str,
) -> str:
    value = _get_secret(secrets, key=key, env_var=env_var)
    if value:
        return value
    raise ValueError(f"Missing secret `{key}` (or env var {env_var})")


def create_llm_provider(settings: AppSettings, *, secrets: SecretStore) -> LLMProvider:
    if settings.provider.llm == LLMProviderName.GEMINI:
        api_key = require_secret(secrets, key="google_api_key", env_var="GOOGLE_API_KEY")
        base: LLMProvider = GeminiLLMProvider(
            api_key=api_key,
            model=settings.gemini.llm_model.value,
        )
    elif settings.provider.llm == LLMProviderName.QWEN:
        from puripuly_heart.config.settings import QwenRegion

        if settings.qwen.region == QwenRegion.BEIJING:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_beijing",
                env_vars=("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        else:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_singapore",
                env_vars=("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        if settings.stt.low_latency_mode:
            # Low-latency mode: use httpx async client for immediate cancellation
            base_url = settings.qwen.get_llm_base_url()
            # Convert SDK URL to OpenAI-compatible URL
            async_base_url = base_url.replace("/api/v1", "/compatible-mode/v1")
            base = AsyncQwenLLMProvider(
                api_key=api_key,
                base_url=async_base_url,
                model=settings.qwen.llm_model.value,
            )
        else:
            # Standard mode: use DashScope SDK
            base = QwenLLMProvider(
                api_key=api_key,
                base_url=settings.qwen.get_llm_base_url(),
                model=settings.qwen.llm_model.value,
            )
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.provider.llm}")

    return SemaphoreLLMProvider(
        inner=base,
        semaphore=asyncio.Semaphore(settings.llm.concurrency_limit),
    )


def create_stt_backend(settings: AppSettings, *, secrets: SecretStore) -> STTBackend:
    effective_terms = get_effective_custom_terms(settings, settings.languages.source_language)

    if settings.provider.stt == STTProviderName.LOCAL_QWEN:
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
        from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

        return LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            stream_label="self",
        )

    if settings.provider.stt == STTProviderName.DEEPGRAM:
        api_key = require_secret(secrets, key="deepgram_api_key", env_var="DEEPGRAM_API_KEY")
        return _create_deepgram_stt_backend(
            settings=settings,
            api_key=api_key,
            keyterms=effective_terms,
        )

    if settings.provider.stt == STTProviderName.QWEN_ASR:
        from puripuly_heart.config.settings import QwenRegion
        from puripuly_heart.core.language import get_qwen_asr_language
        from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend

        if settings.qwen.region == QwenRegion.BEIJING:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_beijing",
                env_vars=("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        else:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_singapore",
                env_vars=("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
        endpoint = settings.qwen.get_asr_endpoint()
        return QwenASRRealtimeSTTBackend(
            api_key=api_key,
            model=settings.qwen_asr_stt.model,
            endpoint=endpoint,
            language=get_qwen_asr_language(settings.languages.source_language),
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
        )

    if settings.provider.stt == STTProviderName.SONIOX:
        from puripuly_heart.core.language import get_soniox_language_hints
        from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend

        api_key = require_secret(secrets, key="soniox_api_key", env_var="SONIOX_API_KEY")
        return SonioxRealtimeSTTBackend(
            api_key=api_key,
            model=settings.soniox_stt.model,
            endpoint=settings.soniox_stt.endpoint,
            language_hints=get_soniox_language_hints(settings.languages.source_language),
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            keepalive_interval_s=settings.soniox_stt.keepalive_interval_s,
            trailing_silence_ms=settings.soniox_stt.trailing_silence_ms,
            context_terms=effective_terms,
        )

    raise ValueError(f"Unsupported STT provider: {settings.provider.stt}")


def resolve_peer_stt_config(settings: AppSettings) -> ResolvedPeerSTTConfig:
    peer_source_language = settings.languages.effective_peer_source
    keyterms = tuple(get_effective_custom_terms(settings, peer_source_language))
    provider = settings.provider.peer_stt

    if provider == STTProviderName.DEEPGRAM:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            keyterms=keyterms,
            deepgram_model=settings.deepgram_stt.model,
        )

    if provider == STTProviderName.QWEN_ASR:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            keyterms=keyterms,
            qwen_model=settings.peer_qwen_asr_stt.model or settings.qwen_asr_stt.model,
            qwen_region=settings.peer_qwen_asr_stt.region or settings.qwen.region,
        )

    if provider == STTProviderName.SONIOX:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            keyterms=keyterms,
            soniox_model=settings.peer_soniox_stt.model or settings.soniox_stt.model,
            soniox_endpoint=settings.peer_soniox_stt.endpoint or settings.soniox_stt.endpoint,
            soniox_keepalive_interval_s=(
                settings.peer_soniox_stt.keepalive_interval_s
                if settings.peer_soniox_stt.keepalive_interval_s is not None
                else settings.soniox_stt.keepalive_interval_s
            ),
            soniox_trailing_silence_ms=(
                settings.peer_soniox_stt.trailing_silence_ms
                if settings.peer_soniox_stt.trailing_silence_ms is not None
                else settings.soniox_stt.trailing_silence_ms
            ),
        )

    if provider == STTProviderName.LOCAL_QWEN:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=settings.audio.internal_sample_rate_hz,
            keyterms=keyterms,
        )

    raise ValueError(f"Unsupported peer STT provider: {provider}")


def build_peer_stt_provider_signature(settings: AppSettings) -> tuple[object, ...]:
    resolved = resolve_peer_stt_config(settings)
    return (
        resolved.provider,
        resolved.source_language,
        resolved.sample_rate_hz,
        resolved.deepgram_model,
        resolved.qwen_model,
        resolved.qwen_region,
        resolved.soniox_model,
        resolved.soniox_endpoint,
        resolved.soniox_keepalive_interval_s,
        resolved.soniox_trailing_silence_ms,
        resolved.keyterms,
    )


def create_peer_stt_backend(settings: AppSettings, *, secrets: SecretStore) -> STTBackend:
    resolved = resolve_peer_stt_config(settings)

    if resolved.provider == STTProviderName.DEEPGRAM:
        api_key = require_secret(secrets, key="deepgram_api_key", env_var="DEEPGRAM_API_KEY")
        return _create_deepgram_stt_backend(
            settings=settings,
            api_key=api_key,
            keyterms=resolved.keyterms,
            source_language=resolved.source_language,
            stream_label="peer",
            model=resolved.deepgram_model,
        )

    if resolved.provider == STTProviderName.QWEN_ASR:
        from puripuly_heart.core.language import get_qwen_asr_language
        from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend

        if resolved.qwen_region == QwenRegion.BEIJING:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_beijing",
                env_vars=("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
            endpoint = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        else:
            api_key = require_secret_any(
                secrets,
                key="alibaba_api_key_singapore",
                env_vars=("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
                legacy_keys=("alibaba_api_key",),
            )
            endpoint = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

        return QwenASRRealtimeSTTBackend(
            api_key=api_key,
            model=resolved.qwen_model,
            endpoint=endpoint,
            language=get_qwen_asr_language(resolved.source_language),
            sample_rate_hz=resolved.sample_rate_hz,
        )

    if resolved.provider == STTProviderName.SONIOX:
        from puripuly_heart.core.language import get_soniox_language_hints
        from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend

        api_key = require_secret(secrets, key="soniox_api_key", env_var="SONIOX_API_KEY")
        return SonioxRealtimeSTTBackend(
            api_key=api_key,
            model=resolved.soniox_model,
            endpoint=resolved.soniox_endpoint,
            language_hints=get_soniox_language_hints(resolved.source_language),
            sample_rate_hz=resolved.sample_rate_hz,
            keepalive_interval_s=resolved.soniox_keepalive_interval_s,
            trailing_silence_ms=resolved.soniox_trailing_silence_ms,
            context_terms=resolved.keyterms,
        )

    if resolved.provider == STTProviderName.LOCAL_QWEN:
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
        from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

        return LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=resolved.sample_rate_hz,
            stream_label="peer",
        )

    raise ValueError(f"Unsupported peer STT provider: {resolved.provider}")


def _create_deepgram_stt_backend(
    *,
    settings: AppSettings,
    api_key: str,
    keyterms: tuple[str, ...] | list[str],
    source_language: str | None = None,
    stream_label: str | None = None,
    model: str | None = None,
) -> STTBackend:
    from puripuly_heart.core.language import get_deepgram_language
    from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend

    source_language = source_language or settings.languages.source_language
    return DeepgramRealtimeSTTBackend(
        api_key=api_key,
        model=model or settings.deepgram_stt.model,
        language=get_deepgram_language(source_language),
        sample_rate_hz=settings.audio.internal_sample_rate_hz,
        keyterms=keyterms,
        stream_label=stream_label,
    )
