from __future__ import annotations

from puripuly_heart.app import wiring_llm_factory as _llm_factory
from puripuly_heart.app.wiring_composition import create_provider_verifier
from puripuly_heart.app.wiring_llm_factory import (
    MANAGED_OPENROUTER_RELEASE_SERVICE_REQUIRED_ERROR,
    _LazyFactoryLLMProvider,
)
from puripuly_heart.app.wiring_managed_auth_factory import (
    ManagedIdentityStateAdapter,
    build_managed_identity_state_port,
    build_openrouter_credential_runtime_config,
    build_openrouter_release_runtime_config,
)
from puripuly_heart.app.wiring_overlay_factory import resolve_overlay_config
from puripuly_heart.app.wiring_secrets_factory import (
    SECRETS_PASSPHRASE_ENV,
    create_secret_store,
    require_secret,
    require_secret_any,
)
from puripuly_heart.app.wiring_stt_factory import (
    ResolvedPeerSTTConfig,
    build_custom_vocabulary_runtime_config,
    build_peer_stt_provider_signature,
    create_peer_stt_backend,
    create_peer_stt_backend_from_resolved_config,
    create_stt_backend,
    create_stt_backend_from_resolved_config,
    resolve_peer_stt_config,
    resolve_peer_stt_runtime_config,
)
from puripuly_heart.config.runtime_resolution import resolve_llm_config
from puripuly_heart.core.openrouter_credentials import load_managed_openrouter_user_identifier

_WIRING_SECRET_KEYS_FOR_COMPATIBILITY_GUARD = (
    "google_api_key",
    "deepseek_api_key",
    "deepgram_api_key",
    "soniox_api_key",
    "alibaba_api_key_beijing",
    "alibaba_api_key_singapore",
    "alibaba_api_key",
    "local_llm_api_key",
)

_base_llm_provider_from_resolved_config = _llm_factory._base_llm_provider_from_resolved_config
_openrouter_provider_from_resolved_config = _llm_factory._openrouter_provider_from_resolved_config
_openrouter_provider_from_resolved_fields = _llm_factory._openrouter_provider_from_resolved_fields
_qwen_api_key_for_resolved_credential = _llm_factory._qwen_api_key_for_resolved_credential


def create_llm_provider_from_resolved_config(*args, **kwargs):
    _llm_factory.load_managed_openrouter_user_identifier = load_managed_openrouter_user_identifier
    return _llm_factory.create_llm_provider_from_resolved_config(*args, **kwargs)


def create_llm_provider(settings, **kwargs):
    runtime_input = _llm_factory._runtime_resolution_input_from_compatibility_settings(settings)
    resolved = resolve_llm_config(runtime_input)
    return create_llm_provider_from_resolved_config(
        resolved,
        compatibility_settings=settings,
        qwen_low_latency_mode=settings.stt.low_latency_mode,
        **kwargs,
    )


__all__ = (
    "SECRETS_PASSPHRASE_ENV",
    "MANAGED_OPENROUTER_RELEASE_SERVICE_REQUIRED_ERROR",
    "ResolvedPeerSTTConfig",
    "build_peer_stt_provider_signature",
    "create_llm_provider",
    "create_llm_provider_from_resolved_config",
    "create_peer_stt_backend",
    "create_peer_stt_backend_from_resolved_config",
    "create_provider_verifier",
    "create_secret_store",
    "create_stt_backend",
    "create_stt_backend_from_resolved_config",
    "require_secret",
    "require_secret_any",
    "resolve_overlay_config",
    "resolve_peer_stt_config",
    "resolve_peer_stt_runtime_config",
    "ManagedIdentityStateAdapter",
    "build_managed_identity_state_port",
    "build_openrouter_credential_runtime_config",
    "build_openrouter_release_runtime_config",
    "build_custom_vocabulary_runtime_config",
    "_LazyFactoryLLMProvider",
)
