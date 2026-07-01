from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from puripuly_heart.app.wiring_managed_auth_factory import (
    _managed_release_service_for_alias,
    build_openrouter_credential_runtime_config,
)
from puripuly_heart.app.wiring_secrets_factory import require_secret, require_secret_any
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
    AppSettings,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterLLMModel,
    OpenRouterProviderRouting,
    OpenRouterRoutingMode,
    OpenRouterSelectionAlias,
    QwenRegion,
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
from puripuly_heart.core.storage.secrets import SecretStore
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider
from puripuly_heart.providers.llm.gemini import GeminiLLMProvider
from puripuly_heart.providers.llm.local_openai import LocalOpenAICompatibleLLMProvider
from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider
from puripuly_heart.providers.llm.qwen import QwenLLMProvider
from puripuly_heart.providers.llm.qwen_async import AsyncQwenLLMProvider

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
                        build_openrouter_credential_runtime_config(openrouter_settings),
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
                build_openrouter_credential_runtime_config(openrouter_settings),
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
