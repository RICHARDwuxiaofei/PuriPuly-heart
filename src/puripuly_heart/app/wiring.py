from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from puripuly_heart.config.llm_profiles import openrouter_alias_for_fields
from puripuly_heart.config.resolved import (
    CREDENTIAL_SOURCE_MANAGED,
    CREDENTIAL_SOURCE_NONE,
    CREDENTIAL_SOURCE_SECRET_STORE,
    ResolvedCredentialRequirement,
    ResolvedLLMConfig,
)
from puripuly_heart.config.runtime_resolution import (
    CREDENTIAL_REF_OPENROUTER_BYOK,
    CREDENTIAL_REF_OPENROUTER_MANAGED,
    CREDENTIAL_REF_QWEN_BEIJING,
    CREDENTIAL_REF_QWEN_SINGAPORE,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_LOCAL_LLM,
    PROVIDER_OPENROUTER,
    PROVIDER_QWEN,
    TRANSLATION_CONNECTION_OFFICIAL_BYOK,
    TRANSLATION_CONNECTION_OPENROUTER,
    TRANSLATION_MODEL_QWEN_35_PLUS,
    DirectProviderRuntimeIntent,
    RuntimeResolutionInput,
    derive_translation_runtime_intent_from_compatibility,
    normalize_openrouter_runtime_intent,
    normalize_translation_runtime_intent,
    resolve_llm_config,
)
from puripuly_heart.config.settings import (
    STT_INTERNAL_SAMPLE_RATE_HZ,
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterProviderRouting,
    OpenRouterRoutingMode,
    OpenRouterSelectionAlias,
    QwenRegion,
    SecretsBackend,
    SecretsSettings,
    STTProviderName,
)
from puripuly_heart.core.llm import FallbackRacingLLMProvider
from puripuly_heart.core.llm.provider import LLMProvider, SemaphoreLLMProvider
from puripuly_heart.core.openrouter_credentials import (
    OPENROUTER_BYOK_API_KEY_ENV,
    OPENROUTER_BYOK_API_KEY_SECRET,
    OPENROUTER_MANAGED_API_KEY_SECRET,
    load_managed_openrouter_user_identifier,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
    SecretStore,
)
from puripuly_heart.core.stt.backend import STTBackend
from puripuly_heart.core.stt.custom_vocab import get_effective_custom_terms
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider

SECRETS_PASSPHRASE_ENV = "PURIPULY_HEART_SECRETS_PASSPHRASE"
MANAGED_OPENROUTER_RELEASE_SERVICE_REQUIRED_ERROR = (
    "OpenRouter managed mode requires a managed release service; "
    "CLI/headless paths are not wired for managed OpenRouter mode yet"
)


@dataclass(slots=True)
class _LazyFactoryLLMProvider(LLMProvider):
    factory: Callable[[], LLMProvider]
    _delegate: LLMProvider | None = field(init=False, default=None, repr=False)
    _delegate_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _ensure_delegate(self) -> LLMProvider:
        if self._delegate is not None:
            return self._delegate

        async with self._delegate_lock:
            if self._delegate is None:
                self._delegate = self.factory()
            return self._delegate

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        delegate = await self._ensure_delegate()
        return await delegate.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

    async def close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()


def _managed_release_service_for_alias(
    managed_release_service: object | None,
    *,
    alias_settings: AppSettings,
) -> object | None:
    if managed_release_service is None:
        return None

    from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterReleaseService

    if not isinstance(managed_release_service, ManagedOpenRouterReleaseService):
        return managed_release_service

    if (
        managed_release_service.settings.openrouter.selection_alias
        == alias_settings.openrouter.selection_alias
    ):
        return managed_release_service

    return ManagedOpenRouterReleaseService(
        settings=alias_settings,
        secrets=managed_release_service.secrets,
        client=managed_release_service.client,
        persist_settings=lambda _updated: managed_release_service.persist_settings(
            managed_release_service.settings
        ),
        app_version=managed_release_service.app_version,
        raw_hardware_fingerprint_provider=managed_release_service.raw_hardware_fingerprint_provider,
        hardware_hash_provider=managed_release_service._legacy_hardware_hash_provider,
        signed_at_provider=managed_release_service.signed_at_provider,
        monotonic_ms_provider=managed_release_service.monotonic_ms_provider,
    )


def _shared_managed_release_service_for_fallback(
    primary: LLMProvider,
    managed_release_service: object | None,
) -> object | None:
    from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterLLMProvider

    if isinstance(primary, ManagedOpenRouterLLMProvider):
        return primary.release_service
    return managed_release_service


def _runtime_resolution_input_from_compatibility_settings(
    settings: AppSettings,
) -> RuntimeResolutionInput:
    openrouter_intent = normalize_openrouter_runtime_intent(
        provider_llm=settings.provider.llm,
        model=settings.openrouter.llm_model,
        selected_source=settings.openrouter.selected_source,
        selection_alias=settings.openrouter.selection_alias,
        fallback_selection_alias=settings.openrouter.fallback_selection_alias,
        routing_mode=settings.openrouter.routing_mode,
        provider_routing=settings.openrouter.provider_routing,
        broker_base_url=settings.openrouter.broker_base_url,
    )
    translation_intent = derive_translation_runtime_intent_from_compatibility(
        provider_llm=settings.provider.llm,
        openrouter_model=openrouter_intent.model,
        openrouter_selected_source=openrouter_intent.selected_source,
        openrouter_provider_routing=openrouter_intent.provider_routing,
        gemini_model=settings.gemini.llm_model,
        qwen_model=settings.qwen.llm_model,
        deepseek_model=settings.deepseek.llm_model,
        concurrency_limit=settings.llm.concurrency_limit,
    )
    if settings.provider.llm == LLMProviderName.QWEN:
        translation_intent = normalize_translation_runtime_intent(
            model=TRANSLATION_MODEL_QWEN_35_PLUS,
            connection=TRANSLATION_CONNECTION_OFFICIAL_BYOK,
            concurrency_limit=settings.llm.concurrency_limit,
        )
    elif (
        settings.provider.llm == LLMProviderName.OPENROUTER
        and openrouter_intent.selected_source == OpenRouterCredentialSource.NONE.value
    ):
        translation_intent = normalize_translation_runtime_intent(
            model=translation_intent.model,
            connection=TRANSLATION_CONNECTION_OPENROUTER,
            concurrency_limit=settings.llm.concurrency_limit,
        )
    direct_intent = DirectProviderRuntimeIntent(
        gemini_3_flash_model=settings.gemini.llm_model.value,
        gemini_31_flash_lite_model=settings.gemini.llm_model.value,
        deepseek_v4_flash_model=settings.deepseek.llm_model.value,
        deepseek_v4_pro_model=settings.deepseek.llm_model.value,
        qwen_35_plus_model=settings.qwen.llm_model.value,
        qwen_region=settings.qwen.region.value,
        local_llm_backend=settings.local_llm.backend.value,
        local_llm_base_url=settings.local_llm.base_url,
        local_llm_model=settings.local_llm.model,
        local_llm_extra_body=settings.local_llm.extra_body,
    )
    return RuntimeResolutionInput(
        translation=translation_intent,
        openrouter=openrouter_intent,
        direct=direct_intent,
    )


def _plain_resolved_option_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_resolved_option_value(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_resolved_option_value(child) for child in value]
    return value


def _resolved_option_mapping(
    values: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    value = values.get(key)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(option_key): _plain_resolved_option_value(option_value)
        for option_key, option_value in value.items()
    }


def _normalize_secret_value(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _require_openrouter_byok_api_key(secrets: SecretStore) -> str:
    value = _normalize_secret_value(secrets.get(OPENROUTER_BYOK_API_KEY_SECRET))
    if value is not None:
        return value
    value = _normalize_secret_value(os.getenv(OPENROUTER_BYOK_API_KEY_ENV))
    if value is not None:
        return value
    raise ValueError(
        f"Missing secret `{OPENROUTER_BYOK_API_KEY_SECRET}` "
        f"(or env var {OPENROUTER_BYOK_API_KEY_ENV})"
    )


def _openrouter_managed_api_key(secrets: SecretStore) -> str | None:
    return _normalize_secret_value(secrets.get(OPENROUTER_MANAGED_API_KEY_SECRET))


def _openrouter_source_for_resolved_credential(
    credential: ResolvedCredentialRequirement,
) -> OpenRouterCredentialSource:
    if (
        credential.source == CREDENTIAL_SOURCE_MANAGED
        or credential.reference == CREDENTIAL_REF_OPENROUTER_MANAGED
    ):
        return OpenRouterCredentialSource.MANAGED
    if (
        credential.source == CREDENTIAL_SOURCE_SECRET_STORE
        and credential.reference == CREDENTIAL_REF_OPENROUTER_BYOK
    ):
        return OpenRouterCredentialSource.BYOK
    if credential.source == CREDENTIAL_SOURCE_NONE:
        return OpenRouterCredentialSource.NONE
    raise ValueError("Unsupported OpenRouter resolved credential reference")


def _settings_for_resolved_openrouter_fields(
    settings: AppSettings | None,
    *,
    model: str,
    service_endpoint: str | None,
    selected_source: OpenRouterCredentialSource,
    provider_routing: OpenRouterProviderRouting,
    routing_mode: OpenRouterRoutingMode,
    include_selection_alias: bool,
) -> AppSettings:
    resolved_settings = replace(settings) if settings is not None else AppSettings()
    resolved_settings.openrouter = replace(resolved_settings.openrouter)
    resolved_settings.openrouter.llm_model = OpenRouterLLMModel(model)
    resolved_settings.openrouter.selected_source = selected_source
    resolved_settings.openrouter.routing_mode = routing_mode
    resolved_settings.openrouter.provider_routing = provider_routing
    resolved_settings.openrouter.broker_base_url = service_endpoint or ""
    selection_alias = None
    if include_selection_alias:
        alias_value = openrouter_alias_for_fields(
            model=model,
            source=selected_source.value,
        )
        if alias_value is not None:
            selection_alias = OpenRouterSelectionAlias(alias_value)
    resolved_settings.openrouter.selection_alias = selection_alias
    return resolved_settings


def _openrouter_routing_mode(value: str | None) -> OpenRouterRoutingMode:
    if value is None:
        return OpenRouterRoutingMode.LATENCY
    return OpenRouterRoutingMode(value)


def _openrouter_provider_routing(value: str | None) -> OpenRouterProviderRouting:
    if value is None:
        return OpenRouterProviderRouting.DEFAULT
    return OpenRouterProviderRouting(value)


def _qwen_api_key_for_resolved_credential(
    credential: ResolvedCredentialRequirement,
    *,
    secrets: SecretStore,
) -> str:
    if credential.reference == CREDENTIAL_REF_QWEN_SINGAPORE:
        return require_secret_any(
            secrets,
            key="alibaba_api_key_singapore",
            env_vars=("ALIBABA_API_KEY_SINGAPORE", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
            legacy_keys=("alibaba_api_key",),
        )
    if credential.reference in (CREDENTIAL_REF_QWEN_BEIJING, None):
        return require_secret_any(
            secrets,
            key="alibaba_api_key_beijing",
            env_vars=("ALIBABA_API_KEY_BEIJING", "ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
            legacy_keys=("alibaba_api_key",),
        )
    raise ValueError("Unsupported Qwen resolved credential reference")


def _qwen_sync_base_url(config: ResolvedLLMConfig) -> str:
    if config.service_endpoint:
        return config.service_endpoint
    if config.region == QwenRegion.SINGAPORE.value:
        return "https://dashscope-intl.aliyuncs.com/api/v1"
    return "https://dashscope.aliyuncs.com/api/v1"


def _qwen_async_base_url(config: ResolvedLLMConfig) -> str:
    sync_base_url = _qwen_sync_base_url(config).rstrip("/")
    if sync_base_url.endswith("/compatible-mode/v1"):
        return sync_base_url
    if sync_base_url.endswith("/api/v1"):
        return sync_base_url[: -len("/api/v1")] + "/compatible-mode/v1"
    return sync_base_url + "/compatible-mode/v1"


def _openrouter_provider_from_resolved_config(
    config: ResolvedLLMConfig,
    *,
    secrets: SecretStore,
    managed_release_service: object | None,
    managed_delegate_ready: Callable[[], object] | None,
    runtime_logging: SessionRuntimeLoggingService | None,
    compatibility_settings: AppSettings | None,
    force_managed_wrapper: bool = False,
    include_selection_alias: bool = True,
) -> LLMProvider:
    return _openrouter_provider_from_resolved_fields(
        model=config.model,
        credential=config.credential,
        service_endpoint=config.service_endpoint,
        routing_mode_value=config.routing_mode,
        provider_routing_value=config.provider_routing,
        secrets=secrets,
        managed_release_service=managed_release_service,
        managed_delegate_ready=managed_delegate_ready,
        runtime_logging=runtime_logging,
        compatibility_settings=compatibility_settings,
        force_managed_wrapper=force_managed_wrapper,
        include_selection_alias=include_selection_alias,
    )


def _openrouter_provider_from_resolved_fields(
    *,
    model: str,
    credential: ResolvedCredentialRequirement,
    service_endpoint: str | None,
    routing_mode_value: str | None,
    provider_routing_value: str | None,
    secrets: SecretStore,
    managed_release_service: object | None,
    managed_delegate_ready: Callable[[], object] | None,
    runtime_logging: SessionRuntimeLoggingService | None,
    compatibility_settings: AppSettings | None,
    force_managed_wrapper: bool = False,
    include_selection_alias: bool = True,
) -> LLMProvider:
    selected_source = _openrouter_source_for_resolved_credential(credential)
    if selected_source == OpenRouterCredentialSource.NONE:
        raise ValueError("OpenRouter selected source must not be `none` for execution")

    routing_mode = _openrouter_routing_mode(routing_mode_value)
    provider_routing = _openrouter_provider_routing(provider_routing_value)
    openrouter_settings = _settings_for_resolved_openrouter_fields(
        compatibility_settings,
        model=model,
        service_endpoint=service_endpoint,
        selected_source=selected_source,
        provider_routing=provider_routing,
        routing_mode=routing_mode,
        include_selection_alias=include_selection_alias,
    )

    if selected_source == OpenRouterCredentialSource.MANAGED:
        if managed_release_service is None:
            raise ValueError(MANAGED_OPENROUTER_RELEASE_SERVICE_REQUIRED_ERROR)
        alias_managed_release_service = _managed_release_service_for_alias(
            managed_release_service,
            alias_settings=openrouter_settings,
        )
        managed_api_key = _openrouter_managed_api_key(secrets)
        if force_managed_wrapper or managed_api_key is None:
            from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterLLMProvider

            return ManagedOpenRouterLLMProvider(
                release_service=alias_managed_release_service,
                delegate_factory=lambda api_key: OpenRouterLLMProvider(
                    api_key=api_key,
                    user_identifier=load_managed_openrouter_user_identifier(
                        openrouter_settings,
                        secrets=secrets,
                    ),
                    model=model,
                    routing_mode=routing_mode,
                    provider_routing=provider_routing,
                    runtime_logging=runtime_logging,
                ),
                on_delegate_ready=managed_delegate_ready,
            )
        return OpenRouterLLMProvider(
            api_key=managed_api_key,
            user_identifier=load_managed_openrouter_user_identifier(
                openrouter_settings,
                secrets=secrets,
            ),
            model=model,
            routing_mode=routing_mode,
            provider_routing=provider_routing,
            runtime_logging=runtime_logging,
        )

    api_key = _require_openrouter_byok_api_key(secrets)
    return OpenRouterLLMProvider(
        api_key=api_key,
        model=model,
        routing_mode=routing_mode,
        provider_routing=provider_routing,
        runtime_logging=runtime_logging,
    )


def _base_llm_provider_from_resolved_config(
    config: ResolvedLLMConfig,
    *,
    secrets: SecretStore,
    managed_release_service: object | None,
    managed_delegate_ready: Callable[[], object] | None,
    runtime_logging: SessionRuntimeLoggingService | None,
    compatibility_settings: AppSettings | None,
    qwen_low_latency_mode: bool,
) -> LLMProvider:
    if config.provider == PROVIDER_GEMINI:
        api_key = require_secret(secrets, key="google_api_key", env_var="GOOGLE_API_KEY")
        return GeminiLLMProvider(
            api_key=api_key,
            model=config.model,
            runtime_logging=runtime_logging,
        )

    if config.provider == PROVIDER_OPENROUTER:
        base = _openrouter_provider_from_resolved_config(
            config,
            secrets=secrets,
            managed_release_service=managed_release_service,
            managed_delegate_ready=managed_delegate_ready,
            runtime_logging=runtime_logging,
            compatibility_settings=compatibility_settings,
        )
        if config.fallback_provider is not None:
            if config.fallback_provider != PROVIDER_OPENROUTER or config.fallback_model is None:
                raise ValueError("Resolved OpenRouter fallback must provide an OpenRouter model")
            fallback_managed_release_service = _shared_managed_release_service_for_fallback(
                base,
                managed_release_service,
            )
            base = FallbackRacingLLMProvider(
                primary=base,
                fallback=_LazyFactoryLLMProvider(
                    factory=lambda: _openrouter_provider_from_resolved_fields(
                        model=config.fallback_model,
                        credential=config.fallback_credential,
                        service_endpoint=config.service_endpoint,
                        routing_mode_value=config.routing_mode,
                        provider_routing_value=config.fallback_provider_routing,
                        secrets=secrets,
                        managed_release_service=fallback_managed_release_service,
                        managed_delegate_ready=managed_delegate_ready,
                        runtime_logging=runtime_logging,
                        compatibility_settings=compatibility_settings,
                        force_managed_wrapper=True,
                        include_selection_alias=False,
                    )
                ),
                runtime_logging=runtime_logging,
            )
        return base

    if config.provider == PROVIDER_QWEN:
        api_key = _qwen_api_key_for_resolved_credential(config.credential, secrets=secrets)
        if qwen_low_latency_mode:
            return AsyncQwenLLMProvider(
                api_key=api_key,
                base_url=_qwen_async_base_url(config),
                model=config.model,
                runtime_logging=runtime_logging,
            )
        return QwenLLMProvider(
            api_key=api_key,
            base_url=_qwen_sync_base_url(config),
            model=config.model,
            runtime_logging=runtime_logging,
        )

    if config.provider == PROVIDER_DEEPSEEK:
        api_key = require_secret(
            secrets,
            key="deepseek_api_key",
            env_var="DEEPSEEK_API_KEY",
        )
        return DeepSeekLLMProvider(
            api_key=api_key,
            model=config.model,
            runtime_logging=runtime_logging,
        )

    if config.provider == PROVIDER_LOCAL_LLM:
        api_key = (secrets.get("local_llm_api_key") or "").strip()
        return LocalOpenAICompatibleLLMProvider(
            base_url=config.base_url or "http://127.0.0.1:11434/v1",
            model=config.model,
            extra_body=_resolved_option_mapping(config.provider_options, "extra_body"),
            api_key=api_key,
            runtime_logging=runtime_logging,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def create_llm_provider_from_resolved_config(
    config: ResolvedLLMConfig,
    *,
    secrets: SecretStore,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: SessionRuntimeLoggingService | None = None,
    compatibility_settings: AppSettings | None = None,
    qwen_low_latency_mode: bool = True,
) -> LLMProvider:
    base = _base_llm_provider_from_resolved_config(
        config,
        secrets=secrets,
        managed_release_service=managed_release_service,
        managed_delegate_ready=managed_delegate_ready,
        runtime_logging=runtime_logging,
        compatibility_settings=compatibility_settings,
        qwen_low_latency_mode=qwen_low_latency_mode,
    )
    return SemaphoreLLMProvider(
        inner=base,
        semaphore=asyncio.Semaphore(config.concurrency_limit),
    )


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


def create_llm_provider(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    managed_release_service: object | None = None,
    managed_delegate_ready: Callable[[], object] | None = None,
    runtime_logging: SessionRuntimeLoggingService | None = None,
) -> LLMProvider:
    runtime_input = _runtime_resolution_input_from_compatibility_settings(settings)
    resolved = resolve_llm_config(runtime_input)
    return create_llm_provider_from_resolved_config(
        resolved,
        secrets=secrets,
        managed_release_service=managed_release_service,
        managed_delegate_ready=managed_delegate_ready,
        runtime_logging=runtime_logging,
        compatibility_settings=settings,
        qwen_low_latency_mode=settings.stt.low_latency_mode,
    )


def create_stt_backend(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    effective_terms = get_effective_custom_terms(settings, settings.languages.source_language)

    if settings.provider.stt == STTProviderName.LOCAL_QWEN:
        from puripuly_heart.core.language import get_local_qwen_language_hint
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
        from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

        return LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            stream_label="self",
            language_hint=get_local_qwen_language_hint(settings.languages.source_language),
            diagnostics_enabled=diagnostics_enabled,
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
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
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
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            keepalive_interval_s=settings.soniox_stt.keepalive_interval_s,
            trailing_silence_ms=settings.soniox_stt.trailing_silence_ms,
            context_terms=effective_terms,
        )

    raise ValueError(f"Unsupported STT provider: {settings.provider.stt}")


def resolve_peer_stt_config(settings: AppSettings) -> ResolvedPeerSTTConfig:
    peer_source_language = settings.languages.effective_peer_source
    keyterms: tuple[str, ...] = ()
    provider = settings.provider.peer_stt

    if provider == STTProviderName.DEEPGRAM:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            keyterms=keyterms,
            deepgram_model=settings.deepgram_stt.model,
        )

    if provider == STTProviderName.QWEN_ASR:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            keyterms=keyterms,
            qwen_model=settings.qwen_asr_stt.model,
            qwen_region=settings.qwen.region,
        )

    if provider == STTProviderName.SONIOX:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            keyterms=keyterms,
            soniox_model=settings.soniox_stt.model,
            soniox_endpoint=settings.soniox_stt.endpoint,
            soniox_keepalive_interval_s=settings.soniox_stt.keepalive_interval_s,
            soniox_trailing_silence_ms=settings.soniox_stt.trailing_silence_ms,
        )

    if provider == STTProviderName.LOCAL_QWEN:
        return ResolvedPeerSTTConfig(
            provider=provider,
            source_language=peer_source_language,
            sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
            keyterms=(),
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


def create_peer_stt_backend(
    settings: AppSettings,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
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
        from puripuly_heart.core.language import get_local_qwen_language_hint
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
        from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

        return LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=resolved.sample_rate_hz,
            stream_label="peer",
            language_hint=get_local_qwen_language_hint(resolved.source_language),
            diagnostics_enabled=diagnostics_enabled,
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
        sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
        keyterms=keyterms,
        stream_label=stream_label,
    )
