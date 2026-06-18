from __future__ import annotations

import asyncio
import contextlib
import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from puripuly_heart.app.ports.managed_identity_state import (
    ManagedIdentitySnapshot,
    ManagedIdentityStatePort,
)
from puripuly_heart.app.ports.provider_verifier import ProviderVerifierPort
from puripuly_heart.config.llm_profiles import openrouter_alias_for_fields
from puripuly_heart.config.resolved import (
    CREDENTIAL_SOURCE_MANAGED,
    CREDENTIAL_SOURCE_NONE,
    CREDENTIAL_SOURCE_SECRET_STORE,
    ResolvedCredentialRequirement,
    ResolvedLLMConfig,
    ResolvedOverlayConfig,
    ResolvedSTTConfig,
)
from puripuly_heart.config.runtime_resolution import (
    CREDENTIAL_REF_DEEPGRAM_STT,
    CREDENTIAL_REF_OPENROUTER_BYOK,
    CREDENTIAL_REF_OPENROUTER_MANAGED,
    CREDENTIAL_REF_QWEN_BEIJING,
    CREDENTIAL_REF_QWEN_SINGAPORE,
    CREDENTIAL_REF_SONIOX_STT,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_LOCAL_LLM,
    PROVIDER_OPENROUTER,
    PROVIDER_QWEN,
    SONIOX_STT_DEFAULT_KEEPALIVE_INTERVAL_S,
    SONIOX_STT_DEFAULT_TRAILING_SILENCE_MS,
    STT_PROVIDER_DEEPGRAM,
    STT_PROVIDER_LOCAL_QWEN,
    STT_PROVIDER_QWEN_ASR,
    STT_PROVIDER_SONIOX,
    TRANSLATION_CONNECTION_OFFICIAL_BYOK,
    TRANSLATION_CONNECTION_OPENROUTER,
    TRANSLATION_MODEL_QWEN_35_PLUS,
    DirectProviderRuntimeIntent,
    OverlayRuntimeIntent,
    RuntimeResolutionInput,
    STTRuntimeIntent,
    derive_translation_runtime_intent_from_compatibility,
    normalize_openrouter_runtime_intent,
    normalize_translation_runtime_intent,
    resolve_llm_config,
)
from puripuly_heart.config.runtime_resolution import (
    resolve_overlay_config as resolve_overlay_runtime_config,
)
from puripuly_heart.config.runtime_resolution import (
    resolve_stt_config as resolve_stt_runtime_config,
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
from puripuly_heart.core.managed_openrouter_release import (
    OpenRouterReleaseRuntimeConfig,
)
from puripuly_heart.core.openrouter_credentials import (
    OPENROUTER_BYOK_API_KEY_ENV,
    OPENROUTER_BYOK_API_KEY_SECRET,
    OPENROUTER_MANAGED_API_KEY_SECRET,
    OpenRouterCredentialRuntimeConfig,
    load_managed_openrouter_user_identifier,
)
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
    SecretStore,
)
from puripuly_heart.core.stt.backend import STTBackend
from puripuly_heart.core.stt.custom_vocab import (
    CustomVocabularyRuntimeConfig,
    get_effective_custom_terms,
)
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


@dataclass(slots=True)
class ManagedIdentityStateAdapter:
    """Boundary adapter that exposes ``AppSettings`` managed-identity state as a
    ``ManagedIdentityStatePort``.

    Reads and writes proxy directly to ``settings.managed_identity`` so that
    mutations are visible to subsequent reads before ``persist`` is called.
    ``persist`` delegates to the supplied persistence callable, which receives
    the wrapped ``AppSettings`` instance.
    """

    _settings: AppSettings
    _persist: Callable[[AppSettings], None]

    @property
    def installation_id(self) -> str:
        return self._settings.managed_identity.installation_id

    @installation_id.setter
    def installation_id(self, value: str) -> None:
        self._settings.managed_identity.installation_id = value

    @property
    def release_token(self) -> str | None:
        return self._settings.managed_identity.release_token

    @release_token.setter
    def release_token(self, value: str | None) -> None:
        self._settings.managed_identity.release_token = value

    @property
    def release_token_expires_at(self) -> str | None:
        return self._settings.managed_identity.release_token_expires_at

    @release_token_expires_at.setter
    def release_token_expires_at(self, value: str | None) -> None:
        self._settings.managed_identity.release_token_expires_at = value

    @property
    def verified_hardware_hash(self) -> str | None:
        return self._settings.managed_identity.verified_hardware_hash

    @verified_hardware_hash.setter
    def verified_hardware_hash(self, value: str | None) -> None:
        self._settings.managed_identity.verified_hardware_hash = value

    @property
    def verified_hardware_hash_salt_version(self) -> int | None:
        return self._settings.managed_identity.verified_hardware_hash_salt_version

    @verified_hardware_hash_salt_version.setter
    def verified_hardware_hash_salt_version(self, value: int | None) -> None:
        self._settings.managed_identity.verified_hardware_hash_salt_version = value

    @property
    def active_managed_credential_ref(self) -> str | None:
        return self._settings.managed_identity.active_managed_credential_ref

    @active_managed_credential_ref.setter
    def active_managed_credential_ref(self, value: str | None) -> None:
        self._settings.managed_identity.active_managed_credential_ref = value

    @property
    def active_managed_expires_at(self) -> str | None:
        return self._settings.managed_identity.active_managed_expires_at

    @active_managed_expires_at.setter
    def active_managed_expires_at(self, value: str | None) -> None:
        self._settings.managed_identity.active_managed_expires_at = value

    @property
    def founder_letter_seen_credential_ref(self) -> str | None:
        return self._settings.managed_identity.founder_letter_seen_credential_ref

    @founder_letter_seen_credential_ref.setter
    def founder_letter_seen_credential_ref(self, value: str | None) -> None:
        self._settings.managed_identity.founder_letter_seen_credential_ref = value

    @property
    def referral_id(self) -> str | None:
        return self._settings.managed_identity.referral_id

    @referral_id.setter
    def referral_id(self, value: str | None) -> None:
        self._settings.managed_identity.referral_id = value

    def persist(self) -> None:
        self._persist(self._settings)

    def snapshot(self) -> ManagedIdentitySnapshot:
        managed = self._settings.managed_identity
        return ManagedIdentitySnapshot(
            installation_id=managed.installation_id,
            release_token=managed.release_token,
            release_token_expires_at=managed.release_token_expires_at,
            verified_hardware_hash=managed.verified_hardware_hash,
            verified_hardware_hash_salt_version=managed.verified_hardware_hash_salt_version,
            active_managed_credential_ref=managed.active_managed_credential_ref,
            active_managed_expires_at=managed.active_managed_expires_at,
            founder_letter_seen_credential_ref=managed.founder_letter_seen_credential_ref,
            referral_id=managed.referral_id,
        )

    def restore(self, snapshot: ManagedIdentitySnapshot) -> None:
        managed = self._settings.managed_identity
        managed.installation_id = snapshot.installation_id
        managed.release_token = snapshot.release_token
        managed.release_token_expires_at = snapshot.release_token_expires_at
        managed.verified_hardware_hash = snapshot.verified_hardware_hash
        managed.verified_hardware_hash_salt_version = snapshot.verified_hardware_hash_salt_version
        managed.active_managed_credential_ref = snapshot.active_managed_credential_ref
        managed.active_managed_expires_at = snapshot.active_managed_expires_at
        managed.founder_letter_seen_credential_ref = snapshot.founder_letter_seen_credential_ref
        managed.referral_id = snapshot.referral_id


def build_managed_identity_state_port(
    settings: AppSettings,
    persist: Callable[[AppSettings], None],
) -> ManagedIdentityStatePort:
    """Build a ``ManagedIdentityStatePort`` adapter at the wiring boundary."""

    return ManagedIdentityStateAdapter(settings, persist)


def build_openrouter_credential_runtime_config(
    settings: AppSettings,
) -> OpenRouterCredentialRuntimeConfig:
    """Build a narrow OpenRouter credential runtime DTO from legacy settings."""

    return OpenRouterCredentialRuntimeConfig(
        selected_source=settings.openrouter.selected_source,
        installation_id=settings.managed_identity.installation_id,
    )


def build_openrouter_release_runtime_config(
    settings: AppSettings,
) -> OpenRouterReleaseRuntimeConfig:
    """Build a narrow OpenRouter release runtime DTO from legacy settings."""

    return OpenRouterReleaseRuntimeConfig(
        llm_model=settings.openrouter.llm_model,
        selected_source=settings.openrouter.selected_source,
        selection_alias=settings.openrouter.selection_alias,
    )


def build_custom_vocabulary_runtime_config(
    settings: AppSettings,
) -> CustomVocabularyRuntimeConfig:
    """Build a narrow custom-vocabulary runtime DTO from legacy settings."""

    return CustomVocabularyRuntimeConfig(
        enabled=settings.stt.custom_vocabulary_enabled,
        terms=settings.stt.custom_terms,
    )


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
        managed_release_service.openrouter_config.selection_alias
        == alias_settings.openrouter.selection_alias
    ):
        return managed_release_service

    return ManagedOpenRouterReleaseService(
        openrouter_config=build_openrouter_release_runtime_config(alias_settings),
        managed_state=ManagedIdentityStateAdapter(
            alias_settings,
            lambda _settings: managed_release_service.managed_state.persist(),
        ),
        secrets=managed_release_service.secrets,
        client=managed_release_service.client,
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

    @property
    def model(self) -> str | None:
        if self.provider == STTProviderName.DEEPGRAM:
            return self.deepgram_model
        if self.provider == STTProviderName.QWEN_ASR:
            return self.qwen_model
        if self.provider == STTProviderName.SONIOX:
            return self.soniox_model
        return None

    @property
    def endpoint(self) -> str | None:
        if self.provider == STTProviderName.SONIOX:
            return self.soniox_endpoint
        return None

    @property
    def region(self) -> QwenRegion | None:
        if self.provider == STTProviderName.QWEN_ASR:
            return self.qwen_region
        return None

    @property
    def provider_options(self) -> Mapping[str, object]:
        if self.provider == STTProviderName.SONIOX:
            return {
                "keepalive_interval_s": self.soniox_keepalive_interval_s,
                "trailing_silence_ms": self.soniox_trailing_silence_ms,
            }
        return {}


def _stt_provider_name_or_raise(
    provider: STTProviderName | str,
    *,
    peer: bool,
) -> STTProviderName:
    if isinstance(provider, STTProviderName):
        return provider
    try:
        return STTProviderName(str(provider))
    except ValueError as exc:
        label = "peer STT" if peer else "STT"
        raise ValueError(f"Unsupported {label} provider: {provider}") from exc


def _stt_provider_value_or_raise(
    provider: STTProviderName | str,
    *,
    peer: bool,
) -> str:
    return _stt_provider_name_or_raise(provider, peer=peer).value


def _effective_custom_terms_for_resolved_config(
    settings: AppSettings,
    source_language: str,
) -> Mapping[str, tuple[str, ...]]:
    terms = tuple(
        get_effective_custom_terms(
            build_custom_vocabulary_runtime_config(settings), source_language
        )
    )
    if not terms:
        return {}
    return {source_language: terms}


def _self_stt_runtime_intent_from_compatibility_settings(settings: AppSettings) -> STTRuntimeIntent:
    source_language = settings.languages.source_language
    return STTRuntimeIntent(
        channel="self",
        provider=_stt_provider_value_or_raise(settings.provider.stt, peer=False),
        source_language=source_language,
        input_host_api=settings.audio.input_host_api,
        input_device=settings.audio.input_device,
        output_device=None,
        sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
        channels=settings.audio.internal_channels,
        ring_buffer_ms=settings.audio.ring_buffer_ms,
        drain_timeout_s=settings.stt.drain_timeout_s,
        vad_speech_threshold=settings.stt.vad_speech_threshold,
        vad_hangover_ms=settings.stt.low_latency_vad_hangover_ms,
        vad_pre_roll_ms=500,
        low_latency_enabled=settings.stt.low_latency_mode,
        low_latency_merge_gap_ms=settings.stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=settings.stt.low_latency_spec_retry_max,
        custom_vocabulary_enabled=settings.stt.custom_vocabulary_enabled,
        custom_terms=_effective_custom_terms_for_resolved_config(settings, source_language),
        deepgram_model=settings.deepgram_stt.model,
        qwen_asr_model=settings.qwen_asr_stt.model,
        qwen_region=settings.qwen.region.value,
        soniox_model=settings.soniox_stt.model,
        soniox_endpoint=settings.soniox_stt.endpoint,
        soniox_keepalive_interval_s=settings.soniox_stt.keepalive_interval_s,
        soniox_trailing_silence_ms=settings.soniox_stt.trailing_silence_ms,
    )


def _peer_stt_runtime_intent_from_compatibility_settings(settings: AppSettings) -> STTRuntimeIntent:
    return STTRuntimeIntent(
        channel="peer",
        provider=_stt_provider_value_or_raise(settings.provider.peer_stt, peer=True),
        source_language=settings.languages.effective_peer_source,
        input_host_api=None,
        input_device=None,
        output_device=settings.desktop_audio.output_device,
        sample_rate_hz=STT_INTERNAL_SAMPLE_RATE_HZ,
        channels=settings.audio.internal_channels,
        ring_buffer_ms=settings.audio.ring_buffer_ms,
        drain_timeout_s=settings.stt.drain_timeout_s,
        vad_speech_threshold=settings.desktop_audio.vad_speech_threshold,
        vad_hangover_ms=settings.desktop_audio.vad_hangover_ms,
        vad_pre_roll_ms=settings.desktop_audio.vad_pre_roll_ms,
        low_latency_enabled=settings.stt.low_latency_mode,
        low_latency_merge_gap_ms=settings.stt.low_latency_merge_gap_ms,
        low_latency_spec_retry_max=settings.stt.low_latency_spec_retry_max,
        custom_vocabulary_enabled=False,
        custom_terms={},
        deepgram_model=settings.deepgram_stt.model,
        qwen_asr_model=settings.qwen_asr_stt.model,
        qwen_region=settings.qwen.region.value,
        soniox_model=settings.soniox_stt.model,
        soniox_endpoint=settings.soniox_stt.endpoint,
        soniox_keepalive_interval_s=settings.soniox_stt.keepalive_interval_s,
        soniox_trailing_silence_ms=settings.soniox_stt.trailing_silence_ms,
    )


def _desktop_overlay_options_from_settings(settings: AppSettings) -> dict[str, object]:
    desktop_settings = copy.deepcopy(settings.overlay.desktop_flet)
    desktop_settings.validate()
    visual = desktop_settings.visual
    return {
        "size_preset": desktop_settings.size_preset,
        "position": {
            "x": desktop_settings.position.x,
            "y": desktop_settings.position.y,
        },
        "locked": desktop_settings.locked,
        "visual": {
            "text_scale": visual.text_scale,
            "background_alpha": visual.background_alpha,
            "outline_width": visual.outline_width,
        },
    }


def resolve_overlay_config(settings: AppSettings) -> ResolvedOverlayConfig:
    return resolve_overlay_runtime_config(
        OverlayRuntimeIntent(
            enabled=settings.ui.overlay_enabled,
            target=settings.overlay.target,
            show_translation=settings.overlay.show_translation,
            show_peer_original=settings.overlay.show_peer_original,
            calibration=settings.overlay.calibration.to_dict(),
            desktop_overlay_options=_desktop_overlay_options_from_settings(settings),
        )
    )


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
    resolved = resolve_stt_runtime_config(
        _self_stt_runtime_intent_from_compatibility_settings(settings)
    )
    return create_stt_backend_from_resolved_config(
        resolved,
        secrets=secrets,
        diagnostics_enabled=diagnostics_enabled,
    )


def _resolved_stt_keyterms(config: ResolvedSTTConfig) -> tuple[str, ...]:
    if not config.custom_vocabulary_enabled:
        return ()
    exact_terms = config.custom_terms.get(config.source_language)
    if exact_terms is not None:
        return tuple(exact_terms)
    base_language = config.source_language.split("-")[0].lower()
    return tuple(config.custom_terms.get(base_language, ()))


def _resolved_float_option(
    options: Mapping[str, object],
    key: str,
    *,
    default: float,
) -> float:
    value = options.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _resolved_int_option(
    options: Mapping[str, object],
    key: str,
    *,
    default: int,
) -> int:
    value = options.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _deepgram_api_key_for_resolved_credential(
    credential: ResolvedCredentialRequirement,
    *,
    secrets: SecretStore,
) -> str:
    if credential.reference not in (CREDENTIAL_REF_DEEPGRAM_STT, None):
        raise ValueError("Unsupported Deepgram resolved credential reference")
    return require_secret(secrets, key="deepgram_api_key", env_var="DEEPGRAM_API_KEY")


def _soniox_api_key_for_resolved_credential(
    credential: ResolvedCredentialRequirement,
    *,
    secrets: SecretStore,
) -> str:
    if credential.reference not in (CREDENTIAL_REF_SONIOX_STT, None):
        raise ValueError("Unsupported Soniox resolved credential reference")
    return require_secret(secrets, key="soniox_api_key", env_var="SONIOX_API_KEY")


def _qwen_asr_endpoint_for_resolved_config(config: ResolvedSTTConfig) -> str:
    if config.endpoint:
        return config.endpoint
    if config.region == QwenRegion.SINGAPORE.value or (
        config.credential.reference == CREDENTIAL_REF_QWEN_SINGAPORE
    ):
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


def create_stt_backend_from_resolved_config(
    config: ResolvedSTTConfig,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    stream_label = config.channel
    keyterms = _resolved_stt_keyterms(config)

    if config.provider == STT_PROVIDER_LOCAL_QWEN:
        from puripuly_heart.core.language import get_local_qwen_language_hint
        from puripuly_heart.core.local_stt_assets import default_local_stt_model_dir
        from puripuly_heart.providers.stt.local_qwen_sherpa import LocalQwenSherpaSTTBackend

        return LocalQwenSherpaSTTBackend(
            model_dir=default_local_stt_model_dir(),
            sample_rate_hz=config.sample_rate_hz,
            stream_label=stream_label,
            language_hint=get_local_qwen_language_hint(config.source_language),
            diagnostics_enabled=diagnostics_enabled,
        )

    if config.provider == STT_PROVIDER_DEEPGRAM:
        from puripuly_heart.core.language import get_deepgram_language
        from puripuly_heart.providers.stt.deepgram import DeepgramRealtimeSTTBackend

        api_key = _deepgram_api_key_for_resolved_credential(config.credential, secrets=secrets)
        return DeepgramRealtimeSTTBackend(
            api_key=api_key,
            model=config.model or "nova-3",
            language=get_deepgram_language(config.source_language),
            sample_rate_hz=config.sample_rate_hz,
            keyterms=keyterms,
            stream_label=stream_label,
        )

    if config.provider == STT_PROVIDER_QWEN_ASR:
        from puripuly_heart.core.language import get_qwen_asr_language
        from puripuly_heart.providers.stt.qwen_asr import QwenASRRealtimeSTTBackend

        api_key = _qwen_api_key_for_resolved_credential(config.credential, secrets=secrets)
        return QwenASRRealtimeSTTBackend(
            api_key=api_key,
            model=config.model or "qwen3-asr-flash-realtime",
            endpoint=_qwen_asr_endpoint_for_resolved_config(config),
            language=get_qwen_asr_language(config.source_language),
            sample_rate_hz=config.sample_rate_hz,
        )

    if config.provider == STT_PROVIDER_SONIOX:
        from puripuly_heart.core.language import get_soniox_language_hints
        from puripuly_heart.providers.stt.soniox import SonioxRealtimeSTTBackend

        api_key = _soniox_api_key_for_resolved_credential(config.credential, secrets=secrets)
        return SonioxRealtimeSTTBackend(
            api_key=api_key,
            model=config.model or "stt-rt-v4",
            endpoint=config.endpoint or "wss://stt-rt.soniox.com/transcribe-websocket",
            language_hints=get_soniox_language_hints(config.source_language),
            sample_rate_hz=config.sample_rate_hz,
            keepalive_interval_s=_resolved_float_option(
                config.provider_options,
                "keepalive_interval_s",
                default=SONIOX_STT_DEFAULT_KEEPALIVE_INTERVAL_S,
            ),
            trailing_silence_ms=_resolved_int_option(
                config.provider_options,
                "trailing_silence_ms",
                default=SONIOX_STT_DEFAULT_TRAILING_SILENCE_MS,
            ),
            context_terms=keyterms,
        )

    raise ValueError(f"Unsupported STT provider: {config.provider}")


def resolve_peer_stt_config(settings: AppSettings) -> ResolvedPeerSTTConfig:
    peer_source_language = settings.languages.effective_peer_source
    keyterms: tuple[str, ...] = ()
    provider = _stt_provider_name_or_raise(settings.provider.peer_stt, peer=True)

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


def _resolved_peer_stt_config_from_compatibility_settings(
    settings: AppSettings,
) -> ResolvedSTTConfig:
    return resolve_stt_runtime_config(
        _peer_stt_runtime_intent_from_compatibility_settings(settings)
    )


def resolve_peer_stt_runtime_config(settings: AppSettings) -> ResolvedSTTConfig:
    return _resolved_peer_stt_config_from_compatibility_settings(settings)


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
    resolved = resolve_peer_stt_runtime_config(settings)
    return create_peer_stt_backend_from_resolved_config(
        resolved,
        secrets=secrets,
        diagnostics_enabled=diagnostics_enabled,
    )


def create_peer_stt_backend_from_resolved_config(
    config: ResolvedSTTConfig,
    *,
    secrets: SecretStore,
    diagnostics_enabled: Callable[[], bool] | None = None,
) -> STTBackend:
    return create_stt_backend_from_resolved_config(
        config,
        secrets=secrets,
        diagnostics_enabled=diagnostics_enabled,
    )


def create_provider_verifier() -> ProviderVerifierPort:
    from puripuly_heart.app.adapters.provider_verifier import ProviderVerifierAdapter

    return ProviderVerifierAdapter()
