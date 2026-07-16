from __future__ import annotations

import pytest

from puripuly_heart.app import wiring_secrets_factory
from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.config.settings import SecretsBackend, SecretsSettings
from puripuly_heart.core.storage.secrets import (
    EncryptedFileSecretStore,
    KeyringSecretStore,
)


def test_create_secret_store_keyring_returns_keyring_store(tmp_path):
    store = create_secret_store(
        SecretsSettings(backend=SecretsBackend.KEYRING),
        config_path=tmp_path / "settings.json",
    )

    assert isinstance(store, KeyringSecretStore)
    assert store.service_name == wiring_secrets_factory.STABLE_KEYRING_SERVICE_NAME


def test_create_secret_store_encrypted_file_resolves_relative_path(tmp_path):
    store = create_secret_store(
        SecretsSettings(backend=SecretsBackend.ENCRYPTED_FILE, encrypted_file_path="secrets.json"),
        config_path=tmp_path / "settings.json",
        passphrase="pw",
    )

    assert isinstance(store, EncryptedFileSecretStore)
    assert store.path == tmp_path / "secrets.json"


def test_create_secret_store_encrypted_file_requires_passphrase(tmp_path):
    with pytest.raises(ValueError):
        create_secret_store(
            SecretsSettings(
                backend=SecretsBackend.ENCRYPTED_FILE, encrypted_file_path="secrets.json"
            ),
            config_path=tmp_path / "settings.json",
        )


def test_copy_stable_secrets_to_vnext_namespace_copies_known_keys_without_overwrite(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeKeyringSecretStore:
        stores: dict[tuple[str, str], str] = {}

        def __init__(self, service_name: str) -> None:
            self.service_name = service_name

        def get(self, key: str) -> str | None:
            return self.stores.get((self.service_name, key))

        def set(self, key: str, value: str) -> None:
            self.stores[(self.service_name, key)] = value

        def delete(self, key: str) -> None:
            self.stores.pop((self.service_name, key), None)

    monkeypatch.setattr(
        wiring_secrets_factory,
        "KeyringSecretStore",
        FakeKeyringSecretStore,
    )
    FakeKeyringSecretStore.stores[
        (wiring_secrets_factory.STABLE_KEYRING_SERVICE_NAME, "google_api_key")
    ] = "stable-google"
    FakeKeyringSecretStore.stores[
        (wiring_secrets_factory.STABLE_KEYRING_SERVICE_NAME, "deepseek_api_key")
    ] = "stable-deepseek"
    FakeKeyringSecretStore.stores[
        (wiring_secrets_factory.VNEXT_KEYRING_SERVICE_NAME, "deepseek_api_key")
    ] = "existing-vnext-deepseek"

    result = wiring_secrets_factory.copy_stable_secrets_to_vnext_namespace(
        SecretsSettings(backend=SecretsBackend.KEYRING),
        stable_config_path=tmp_path / "stable" / "settings.json",
        vnext_config_path=tmp_path / "vnext" / "settings.json",
        keys=("google_api_key", "deepseek_api_key", "missing_api_key"),
    )

    assert result.ok
    assert result.copied_keys == ("google_api_key",)
    assert result.skipped_keys == ("deepseek_api_key", "missing_api_key")
    assert result.failed_keys == ()
    assert (
        FakeKeyringSecretStore.stores[
            (wiring_secrets_factory.VNEXT_KEYRING_SERVICE_NAME, "google_api_key")
        ]
        == "stable-google"
    )
    assert (
        FakeKeyringSecretStore.stores[
            (wiring_secrets_factory.VNEXT_KEYRING_SERVICE_NAME, "deepseek_api_key")
        ]
        == "existing-vnext-deepseek"
    )


def test_copy_stable_secrets_to_vnext_namespace_does_not_create_missing_stable_file(
    tmp_path,
) -> None:
    stable_config_path = tmp_path / "stable" / "settings.json"
    vnext_config_path = tmp_path / "vnext" / "settings.json"
    stable_secret_path = stable_config_path.parent / "secrets.json"
    vnext_secret_path = vnext_config_path.parent / "secrets.json"

    result = wiring_secrets_factory.copy_stable_secrets_to_vnext_namespace(
        SecretsSettings(
            backend=SecretsBackend.ENCRYPTED_FILE,
            encrypted_file_path="secrets.json",
        ),
        stable_config_path=stable_config_path,
        vnext_config_path=vnext_config_path,
        passphrase="pw",
        keys=("google_api_key",),
    )

    assert result.ok
    assert result.copied_keys == ()
    assert result.skipped_keys == ("google_api_key",)
    assert not stable_secret_path.exists()
    assert not vnext_secret_path.exists()
