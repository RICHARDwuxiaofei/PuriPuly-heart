from __future__ import annotations

import pytest

from puripuly_heart.config.settings import AppSettings, OpenRouterCredentialSource
from puripuly_heart.core.openrouter_credentials import (
    OPENROUTER_BYOK_API_KEY_SECRET,
    OPENROUTER_MANAGED_API_KEY_SECRET,
    OpenRouterManagedRecoveryAction,
    clear_temporary_managed_release_state,
    handle_managed_availability,
    handle_managed_release_error,
    resolve_openrouter_credentials,
)
from puripuly_heart.core.storage.secrets import InMemorySecretStore


def test_resolve_openrouter_credentials_respects_none_selection_even_with_stored_keys() -> None:
    settings = AppSettings()
    store = InMemorySecretStore()
    store.set(OPENROUTER_BYOK_API_KEY_SECRET, "byok-key")
    store.set(OPENROUTER_MANAGED_API_KEY_SECRET, "managed-key")

    resolution = resolve_openrouter_credentials(settings, secrets=store)

    assert resolution.selected_source == OpenRouterCredentialSource.NONE
    assert resolution.api_key is None
    assert resolution.requires_managed_challenge is False


def test_resolve_openrouter_credentials_uses_selected_byok_key_without_managed_fallback() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    store = InMemorySecretStore()
    store.set(OPENROUTER_BYOK_API_KEY_SECRET, "byok-key")
    store.set(OPENROUTER_MANAGED_API_KEY_SECRET, "managed-key")

    resolution = resolve_openrouter_credentials(settings, secrets=store)

    assert resolution.selected_source == OpenRouterCredentialSource.BYOK
    assert resolution.api_key == "byok-key"
    assert resolution.requires_managed_challenge is False


def test_resolve_openrouter_credentials_uses_selected_managed_key_without_byok_fallback() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    store = InMemorySecretStore()
    store.set(OPENROUTER_BYOK_API_KEY_SECRET, "byok-key")
    store.set(OPENROUTER_MANAGED_API_KEY_SECRET, "managed-key")

    resolution = resolve_openrouter_credentials(settings, secrets=store)

    assert resolution.selected_source == OpenRouterCredentialSource.MANAGED
    assert resolution.api_key == "managed-key"
    assert resolution.requires_managed_challenge is False


def test_resolve_openrouter_credentials_requires_explicit_trans_intent_before_managed_release() -> (
    None
):
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    store = InMemorySecretStore()
    store.set(OPENROUTER_BYOK_API_KEY_SECRET, "byok-key")

    resolution = resolve_openrouter_credentials(settings, secrets=store)
    trans_resolution = resolve_openrouter_credentials(
        settings,
        secrets=store,
        request_intent="TRANS",
    )

    assert resolution.api_key is None
    assert resolution.requires_managed_challenge is False
    assert trans_resolution.api_key is None
    assert trans_resolution.requires_managed_challenge is True


def test_clear_temporary_managed_release_state_clears_verified_snapshot_fields() -> None:
    settings = AppSettings()
    settings.managed_identity.release_token = "release-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:00:45.000Z"
    settings.managed_identity.verified_hardware_hash = "hardware-hash-1"
    settings.managed_identity.verified_hardware_hash_salt_version = 7

    clear_temporary_managed_release_state(settings)

    assert settings.managed_identity.release_token is None
    assert settings.managed_identity.release_token_expires_at is None
    assert settings.managed_identity.verified_hardware_hash is None
    assert settings.managed_identity.verified_hardware_hash_salt_version is None


@pytest.mark.parametrize(
    ("selected_source", "managed_availability"),
    [
        (OpenRouterCredentialSource.MANAGED, "not_eligible"),
        (OpenRouterCredentialSource.BYOK, "unavailable"),
    ],
)
def test_handle_managed_availability_stops_flow_without_switching_sources(
    selected_source: OpenRouterCredentialSource,
    managed_availability: str,
) -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = selected_source
    settings.managed_identity.release_token = "release-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:00:45.000Z"

    result = handle_managed_availability(
        settings,
        managed_availability=managed_availability,
    )

    assert result.action == OpenRouterManagedRecoveryAction.STOP
    assert result.reason == managed_availability
    assert result.selected_source == selected_source
    assert result.managed_availability == managed_availability
    assert settings.openrouter.selected_source == selected_source
    assert settings.managed_identity.release_token is None
    assert settings.managed_identity.release_token_expires_at is None


@pytest.mark.parametrize(
    ("selected_source", "error_code"),
    [
        (OpenRouterCredentialSource.MANAGED, "release_token_expired"),
        (OpenRouterCredentialSource.BYOK, "security_fail"),
    ],
)
def test_handle_managed_release_error_restarts_from_challenge_without_switching_sources(
    selected_source: OpenRouterCredentialSource,
    error_code: str,
) -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = selected_source
    settings.managed_identity.release_token = "release-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:00:45.000Z"

    result = handle_managed_release_error(settings, error_code=error_code)

    assert result.action == OpenRouterManagedRecoveryAction.RESTART_CHALLENGE
    assert result.reason == error_code
    assert result.selected_source == selected_source
    assert result.managed_availability is None
    assert settings.openrouter.selected_source == selected_source
    assert settings.managed_identity.release_token is None
    assert settings.managed_identity.release_token_expires_at is None
