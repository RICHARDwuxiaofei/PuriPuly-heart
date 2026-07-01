from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from puripuly_heart.app.ports.managed_identity_state import (
    ManagedIdentitySnapshot,
    ManagedIdentityStatePort,
)
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.core.managed_openrouter_release import OpenRouterReleaseRuntimeConfig
from puripuly_heart.core.openrouter_credentials import OpenRouterCredentialRuntimeConfig


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
