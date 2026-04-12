from __future__ import annotations

import pytest

from puripuly_heart.app.wiring import (
    build_peer_stt_provider_signature,
    create_llm_provider,
    create_peer_stt_backend,
    create_stt_backend,
    resolve_peer_stt_config,
)
from puripuly_heart.config.settings import (
    AppSettings,
    DeepgramSTTSettings,
    GeminiLLMModel,
    GeminiSettings,
    LLMProviderName,
    LLMSettings,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterRoutingMode,
    OpenRouterSettings,
    ProviderSettings,
    QwenASRSTTSettings,
    QwenLLMModel,
    QwenRegion,
    QwenSettings,
    SonioxSTTSettings,
    STTProviderName,
    STTSettings,
)
from puripuly_heart.core.language import (
    get_deepgram_language,
    get_qwen_asr_language,
)
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterLLMProvider
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider
from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend
from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend
from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend
from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend


def test_create_llm_provider_gemini_uses_secret_and_concurrency_limit() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.GEMINI),
        llm=LLMSettings(concurrency_limit=3),
    )
    secrets = InMemorySecretStore()
    secrets.set("google_api_key", "k")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, GeminiLLMProvider)
    assert provider.inner.api_key == "k"
    assert provider.inner.model == "gemini-3.1-flash-lite-preview"
    assert provider.semaphore._value == 3  # type: ignore[attr-defined]


def test_create_llm_provider_gemini_uses_selected_model() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.GEMINI),
        gemini=GeminiSettings(llm_model=GeminiLLMModel.GEMINI_31_FLASH_LITE),
    )
    secrets = InMemorySecretStore()
    secrets.set("google_api_key", "k")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, GeminiLLMProvider)
    assert provider.inner.model == "gemini-3.1-flash-lite-preview"


def test_create_llm_provider_gemini_passes_runtime_logging() -> None:
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.GEMINI))
    secrets = InMemorySecretStore()
    secrets.set("google_api_key", "k")
    runtime_logging = object()

    provider = create_llm_provider(settings, secrets=secrets, runtime_logging=runtime_logging)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, GeminiLLMProvider)
    assert provider.inner.runtime_logging is runtime_logging


def test_create_llm_provider_qwen_uses_secret() -> None:
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.QWEN))
    secrets = InMemorySecretStore()
    # Default region is Beijing, so we need alibaba_api_key_beijing
    secrets.set("alibaba_api_key_beijing", "k2")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, AsyncQwenLLMProvider)
    assert provider.inner.api_key == "k2"
    assert provider.inner.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert provider.inner.model == "qwen3.5-plus"
    assert provider.semaphore._value == 5  # type: ignore[attr-defined]


def test_create_llm_provider_qwen_low_latency_passes_runtime_logging() -> None:
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.QWEN))
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_beijing", "k2")
    runtime_logging = object()

    provider = create_llm_provider(settings, secrets=secrets, runtime_logging=runtime_logging)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, AsyncQwenLLMProvider)
    assert provider.inner.runtime_logging is runtime_logging


def test_create_llm_provider_qwen_uses_singapore_region() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.QWEN),
        qwen=QwenSettings(region=QwenRegion.SINGAPORE, llm_model=QwenLLMModel.QWEN_35_PLUS),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_singapore", "k3")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, AsyncQwenLLMProvider)
    assert provider.inner.api_key == "k3"
    assert provider.inner.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert provider.inner.model == "qwen3.5-plus"


def test_create_llm_provider_qwen_uses_legacy_alibaba_secret_key() -> None:
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.QWEN))
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key", "legacy-k2")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, AsyncQwenLLMProvider)
    assert provider.inner.api_key == "legacy-k2"
    # Legacy key should be backfilled to region-specific key for future runs.
    assert secrets.get("alibaba_api_key_beijing") == "legacy-k2"


def test_create_llm_provider_qwen_standard_mode_uses_sync_provider() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.QWEN),
        stt=STTSettings(low_latency_mode=False),
        qwen=QwenSettings(llm_model=QwenLLMModel.QWEN_35_PLUS),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_beijing", "k2")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, QwenLLMProvider)
    assert provider.inner.api_key == "k2"
    assert provider.inner.base_url == "https://dashscope.aliyuncs.com/api/v1"
    assert provider.inner.model == "qwen3.5-plus"


def test_create_llm_provider_qwen_standard_mode_passes_runtime_logging() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.QWEN),
        stt=STTSettings(low_latency_mode=False),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_beijing", "k2")
    runtime_logging = object()

    provider = create_llm_provider(settings, secrets=secrets, runtime_logging=runtime_logging)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, QwenLLMProvider)
    assert provider.inner.runtime_logging is runtime_logging


def test_create_llm_provider_qwen_standard_mode_singapore() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.QWEN),
        qwen=QwenSettings(region=QwenRegion.SINGAPORE, llm_model=QwenLLMModel.QWEN_35_FLASH),
        stt=STTSettings(low_latency_mode=False),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_singapore", "k3")

    provider = create_llm_provider(settings, secrets=secrets)
    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, QwenLLMProvider)
    assert provider.inner.api_key == "k3"
    assert provider.inner.base_url == "https://dashscope-intl.aliyuncs.com/api/v1"
    assert provider.inner.model == "qwen3.5-flash"


def test_create_llm_provider_openrouter_uses_secret_and_model() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        llm=LLMSettings(concurrency_limit=4),
        openrouter=OpenRouterSettings(
            llm_model=OpenRouterLLMModel.GEMMA_4_26B_A4B_IT,
            routing_mode=OpenRouterRoutingMode.PARASAIL_FIRST,
            selected_source=OpenRouterCredentialSource.BYOK,
        ),
    )
    secrets = InMemorySecretStore()
    secrets.set("openrouter_api_key", "or-key")

    provider = create_llm_provider(settings, secrets=secrets)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, OpenRouterLLMProvider)
    assert provider.inner.api_key == "or-key"
    assert provider.inner.model == "google/gemma-4-26b-a4b-it"
    assert provider.inner.base_url == "https://openrouter.ai/api/v1"
    assert provider.inner.routing_mode == OpenRouterRoutingMode.PARASAIL_FIRST
    assert provider.semaphore._value == 4  # type: ignore[attr-defined]


def test_create_llm_provider_openrouter_passes_runtime_logging() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.BYOK),
    )
    secrets = InMemorySecretStore()
    secrets.set("openrouter_api_key", "or-key")
    runtime_logging = object()

    provider = create_llm_provider(settings, secrets=secrets, runtime_logging=runtime_logging)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, OpenRouterLLMProvider)
    assert provider.inner.runtime_logging is runtime_logging


def test_create_llm_provider_openrouter_uses_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-or-key")
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.BYOK),
    )
    secrets = InMemorySecretStore()

    provider = create_llm_provider(settings, secrets=secrets)

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, OpenRouterLLMProvider)
    assert provider.inner.api_key == "env-or-key"


def test_create_llm_provider_openrouter_uses_selected_managed_key() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )
    secrets = InMemorySecretStore()
    secrets.set("openrouter_api_key", "byok-key")
    secrets.set("openrouter_managed_api_key", "managed-key")
    managed_release_service = object()

    provider = create_llm_provider(
        settings,
        secrets=secrets,
        managed_release_service=managed_release_service,
    )

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, OpenRouterLLMProvider)
    assert provider.inner.api_key == "managed-key"


def test_create_llm_provider_openrouter_requires_release_service_for_managed_mode() -> None:
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )
    secrets = InMemorySecretStore()
    secrets.set("openrouter_api_key", "byok-key")
    secrets.set("openrouter_managed_api_key", "managed-key")

    with pytest.raises(ValueError, match="managed release service"):
        create_llm_provider(settings, secrets=secrets)


def test_create_llm_provider_openrouter_uses_managed_wrapper_when_release_service_is_available() -> (
    None
):
    settings = AppSettings(
        provider=ProviderSettings(llm=LLMProviderName.OPENROUTER),
        openrouter=OpenRouterSettings(selected_source=OpenRouterCredentialSource.MANAGED),
    )
    secrets = InMemorySecretStore()
    managed_release_service = object()
    runtime_logging = object()

    provider = create_llm_provider(
        settings,
        secrets=secrets,
        managed_release_service=managed_release_service,
        runtime_logging=runtime_logging,
    )

    assert isinstance(provider, SemaphoreLLMProvider)
    assert isinstance(provider.inner, ManagedOpenRouterLLMProvider)
    assert provider.inner.release_service is managed_release_service
    delegate = provider.inner.delegate_factory("delegate-key")
    assert isinstance(delegate, OpenRouterLLMProvider)
    assert delegate.runtime_logging is runtime_logging


def test_create_llm_provider_openrouter_rejects_none_selected_source_even_with_keys() -> None:
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.OPENROUTER))
    secrets = InMemorySecretStore()
    secrets.set("openrouter_api_key", "byok-key")
    secrets.set("openrouter_managed_api_key", "managed-key")

    with pytest.raises(ValueError, match="selected source"):
        create_llm_provider(settings, secrets=secrets)


def test_create_llm_provider_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = AppSettings(provider=ProviderSettings(llm=LLMProviderName.GEMINI))
    secrets = InMemorySecretStore()
    with pytest.raises(ValueError):
        create_llm_provider(settings, secrets=secrets)


def test_create_stt_backend_deepgram_uses_settings_and_secret() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.DEEPGRAM),
        deepgram_stt=DeepgramSTTSettings(model="nova-3"),
    )
    settings.audio.internal_sample_rate_hz = 8000
    secrets = InMemorySecretStore()
    secrets.set("deepgram_api_key", "k3")

    backend = create_stt_backend(settings, secrets=secrets)
    assert isinstance(backend, DeepgramRealtimeSTTBackend)
    assert backend.api_key == "k3"
    assert backend.model == "nova-3"
    assert backend.sample_rate_hz == 16000
    assert backend.language == get_deepgram_language(settings.languages.source_language)
    assert list(backend.keyterms) == ["아이리", "시나노"]


def test_create_stt_backend_deepgram_passes_effective_custom_terms() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.DEEPGRAM),
        deepgram_stt=DeepgramSTTSettings(model="nova-3"),
        stt=STTSettings(
            custom_vocabulary_enabled=True,
            custom_terms={"ko": [" Puripuly ", "", "VRChat", "Puripuly"]},
        ),
    )
    secrets = InMemorySecretStore()
    secrets.set("deepgram_api_key", "k3")

    backend = create_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, DeepgramRealtimeSTTBackend)
    assert list(backend.keyterms) == ["Puripuly", "VRChat"]


def test_create_stt_backend_local_qwen_uses_shared_model_path_without_secret() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.LOCAL_QWEN),
    )
    settings.audio.internal_sample_rate_hz = 8000
    secrets = InMemorySecretStore()

    backend = create_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, LocalQwenSherpaSTTBackend)
    assert backend.model_dir == default_local_stt_model_dir()
    assert backend.sample_rate_hz == 16000
    assert backend.stream_label == "self"


def test_create_stt_backend_local_qwen_passes_language_hint_without_hotwords() -> None:
    settings = AppSettings(provider=ProviderSettings(stt=STTProviderName.LOCAL_QWEN))
    settings.languages.source_language = "ko-KR"
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": ["Puripuly", "VRChat, Japan", *[f"term-{i:02d}" for i in range(20)]],
    }
    secrets = InMemorySecretStore()

    backend = create_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, LocalQwenSherpaSTTBackend)
    assert getattr(backend, "language_hint", None) == "Korean"
    assert getattr(backend, "hotwords", ()) == ()


def test_create_peer_stt_backend_uses_dedicated_deepgram_configuration() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.SONIOX),
        deepgram_stt=DeepgramSTTSettings(model="nova-3"),
    )
    settings.audio.internal_sample_rate_hz = 8000
    secrets = InMemorySecretStore()
    secrets.set("deepgram_api_key", "peer-k")

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, DeepgramRealtimeSTTBackend)
    assert backend.api_key == "peer-k"
    assert backend.model == "nova-3"
    assert backend.sample_rate_hz == 16000
    assert backend.language == get_deepgram_language(settings.languages.source_language)
    assert list(backend.keyterms) == ["아이리", "시나노"]
    assert backend.stream_label == "peer"


def test_create_peer_stt_backend_uses_effective_peer_source_language_and_terms() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.SONIOX),
        deepgram_stt=DeepgramSTTSettings(model="nova-3"),
    )
    settings.languages.source_language = "ko"
    settings.languages.peer_source_language = "zh-CN"
    secrets = InMemorySecretStore()
    secrets.set("deepgram_api_key", "peer-k")

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, DeepgramRealtimeSTTBackend)
    assert backend.language == get_deepgram_language(settings.languages.effective_peer_source)
    assert list(backend.keyterms) == ["airi", "shinano"]


def test_self_stt_provider_setting_does_not_change_peer_backend_choice() -> None:
    secrets = InMemorySecretStore()
    secrets.set("deepgram_api_key", "peer-k")

    soniox_settings = AppSettings(provider=ProviderSettings(stt=STTProviderName.SONIOX))
    qwen_settings = AppSettings(provider=ProviderSettings(stt=STTProviderName.QWEN_ASR))

    soniox_backend = create_peer_stt_backend(soniox_settings, secrets=secrets)
    qwen_backend = create_peer_stt_backend(qwen_settings, secrets=secrets)

    assert isinstance(soniox_backend, DeepgramRealtimeSTTBackend)
    assert isinstance(qwen_backend, DeepgramRealtimeSTTBackend)


def test_resolve_peer_stt_config_always_uses_self_deepgram_model() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    settings.deepgram_stt.model = "nova-3-general"

    resolved = resolve_peer_stt_config(settings)

    assert resolved.provider == STTProviderName.DEEPGRAM
    assert resolved.deepgram_model == "nova-3-general"


def test_create_peer_stt_backend_uses_peer_selected_soniox_provider() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.languages.peer_source_language = "ko"
    settings.peer_soniox_stt.model = "stt-rt-v4"
    secrets = InMemorySecretStore()
    secrets.set("soniox_api_key", "peer-soniox")

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, SonioxRealtimeSTTBackend)
    assert backend.api_key == "peer-soniox"
    assert backend.model == "stt-rt-v4"


def test_create_peer_stt_backend_uses_peer_qwen_region_for_endpoint_and_secret() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.peer_qwen_asr_stt.region = QwenRegion.SINGAPORE
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_singapore", "peer-qwen")

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, QwenASRRealtimeSTTBackend)
    assert backend.api_key == "peer-qwen"
    assert backend.endpoint == "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"


def test_build_peer_stt_provider_signature_includes_backend_affecting_values() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.languages.peer_source_language = "zh-CN"
    settings.peer_soniox_stt.model = "stt-rt-v4"
    settings.peer_soniox_stt.trailing_silence_ms = 350

    signature = build_peer_stt_provider_signature(settings)

    assert STTProviderName.SONIOX in signature
    assert "zh-CN" in signature
    assert "stt-rt-v4" in signature
    assert 350 in signature


def test_build_peer_stt_provider_signature_uses_fixed_16khz_runtime_contract() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.audio.internal_sample_rate_hz = 8000

    signature = build_peer_stt_provider_signature(settings)

    assert signature[2] == 16000


def test_resolve_peer_stt_config_inherits_peer_qwen_model_until_override() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.qwen_asr_stt.model = "self-qwen-asr"
    settings.peer_qwen_asr_stt.model = None

    resolved = resolve_peer_stt_config(settings)

    assert resolved.qwen_model == "self-qwen-asr"

    settings.peer_qwen_asr_stt.model = "peer-qwen-asr"

    resolved = resolve_peer_stt_config(settings)

    assert resolved.qwen_model == "peer-qwen-asr"


def test_create_peer_stt_backend_uses_peer_local_qwen_provider_and_fixed_sample_rate() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.audio.internal_sample_rate_hz = 8000
    secrets = InMemorySecretStore()

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, LocalQwenSherpaSTTBackend)
    assert backend.model_dir == default_local_stt_model_dir()
    assert backend.sample_rate_hz == 16000
    assert backend.stream_label == "peer"


def test_managed_stt_provider_rejects_legacy_8khz_runtime_sample_rate() -> None:
    with pytest.raises(ValueError, match="16000"):
        ManagedSTTProvider(backend=None, sample_rate_hz=8000)  # type: ignore[arg-type]


def test_local_qwen_sherpa_backend_rejects_legacy_8khz_runtime_sample_rate() -> None:
    with pytest.raises(ValueError, match="16000"):
        LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=8000,
        )


def test_create_peer_stt_backend_local_qwen_uses_peer_language_without_hotwords() -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    settings.languages.source_language = "ko"
    settings.languages.peer_source_language = "zh-CN"
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "zh-CN": ["airi", "shinano", *[f"term-{i:02d}" for i in range(20)]],
    }
    secrets = InMemorySecretStore()

    backend = create_peer_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, LocalQwenSherpaSTTBackend)
    assert getattr(backend, "language_hint", None) == "Chinese"
    assert getattr(backend, "hotwords", ()) == ()


def test_resolve_peer_stt_config_inherits_soniox_endpoint_keepalive_and_trailing_silence_until_override() -> (
    None
):
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.soniox_stt.model = "self-soniox"
    settings.soniox_stt.endpoint = "wss://self-soniox.example/realtime"
    settings.soniox_stt.keepalive_interval_s = 12.5
    settings.soniox_stt.trailing_silence_ms = 900
    settings.peer_soniox_stt.model = None
    settings.peer_soniox_stt.endpoint = None
    settings.peer_soniox_stt.keepalive_interval_s = None
    settings.peer_soniox_stt.trailing_silence_ms = None

    resolved = resolve_peer_stt_config(settings)

    assert resolved.soniox_model == "self-soniox"
    assert resolved.soniox_endpoint == "wss://self-soniox.example/realtime"
    assert resolved.soniox_keepalive_interval_s == 12.5
    assert resolved.soniox_trailing_silence_ms == 900

    settings.peer_soniox_stt.model = "peer-soniox"
    settings.peer_soniox_stt.endpoint = "wss://peer-soniox.example/realtime"
    settings.peer_soniox_stt.keepalive_interval_s = 6.0
    settings.peer_soniox_stt.trailing_silence_ms = 250

    resolved = resolve_peer_stt_config(settings)

    assert resolved.soniox_model == "peer-soniox"
    assert resolved.soniox_endpoint == "wss://peer-soniox.example/realtime"
    assert resolved.soniox_keepalive_interval_s == 6.0
    assert resolved.soniox_trailing_silence_ms == 250


def test_create_stt_backend_qwen_asr_uses_settings_and_secret() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.QWEN_ASR),
        qwen_asr_stt=QwenASRSTTSettings(
            model="qwen3-asr-flash-realtime",
        ),
    )
    settings.audio.internal_sample_rate_hz = 8000
    secrets = InMemorySecretStore()
    # Default region is Beijing, so we need alibaba_api_key_beijing
    secrets.set("alibaba_api_key_beijing", "k4")

    backend = create_stt_backend(settings, secrets=secrets)
    assert isinstance(backend, QwenASRRealtimeSTTBackend)
    assert backend.api_key == "k4"
    assert backend.model == "qwen3-asr-flash-realtime"
    # Endpoint is derived from region (Beijing default)
    assert backend.endpoint == "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    assert backend.sample_rate_hz == 16000
    assert backend.language == get_qwen_asr_language(settings.languages.source_language)


def test_create_stt_backend_qwen_asr_ignores_custom_terms() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.QWEN_ASR),
        stt=STTSettings(
            custom_vocabulary_enabled=True,
            custom_terms={"ko": ["Puripuly", "VRChat"]},
        ),
        qwen_asr_stt=QwenASRSTTSettings(model="qwen3-asr-flash-realtime"),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_beijing", "k4")

    backend = create_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, QwenASRRealtimeSTTBackend)
    assert backend.api_key == "k4"
    assert backend.model == "qwen3-asr-flash-realtime"
    assert backend.language == get_qwen_asr_language(settings.languages.source_language)
    assert not hasattr(backend, "keyterms")
    assert not hasattr(backend, "context_terms")


def test_create_stt_backend_qwen_asr_uses_singapore_region() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.QWEN_ASR),
        qwen=QwenSettings(region=QwenRegion.SINGAPORE),
        qwen_asr_stt=QwenASRSTTSettings(model="qwen3-asr-flash-realtime"),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key_singapore", "k5")

    backend = create_stt_backend(settings, secrets=secrets)
    assert isinstance(backend, QwenASRRealtimeSTTBackend)
    assert backend.endpoint == "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"


def test_create_stt_backend_qwen_asr_uses_legacy_alibaba_secret_key() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.QWEN_ASR),
        qwen_asr_stt=QwenASRSTTSettings(model="qwen3-asr-flash-realtime"),
    )
    secrets = InMemorySecretStore()
    secrets.set("alibaba_api_key", "legacy-k4")

    backend = create_stt_backend(settings, secrets=secrets)
    assert isinstance(backend, QwenASRRealtimeSTTBackend)
    assert backend.api_key == "legacy-k4"
    # Legacy key should be backfilled to region-specific key for future runs.
    assert secrets.get("alibaba_api_key_beijing") == "legacy-k4"


def test_create_stt_backend_soniox_uses_secret() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.SONIOX),
        soniox_stt=SonioxSTTSettings(model="stt-rt-v4"),
    )
    secrets = InMemorySecretStore()
    secrets.set("soniox_api_key", "k6")

    backend = create_stt_backend(settings, secrets=secrets)
    assert isinstance(backend, SonioxRealtimeSTTBackend)
    assert backend.api_key == "k6"
    assert list(backend.context_terms) == ["아이리", "시나노"]


def test_create_stt_backend_soniox_passes_effective_custom_terms() -> None:
    settings = AppSettings(
        provider=ProviderSettings(stt=STTProviderName.SONIOX),
        soniox_stt=SonioxSTTSettings(model="stt-rt-v4"),
        stt=STTSettings(
            custom_vocabulary_enabled=True,
            custom_terms={"ko": [" Puripuly ", "VRChat", "Puripuly", " "]},
        ),
    )
    secrets = InMemorySecretStore()
    secrets.set("soniox_api_key", "k6")

    backend = create_stt_backend(settings, secrets=secrets)

    assert isinstance(backend, SonioxRealtimeSTTBackend)
    assert list(backend.context_terms) == ["Puripuly", "VRChat"]
