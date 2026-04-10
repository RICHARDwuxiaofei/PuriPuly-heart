from __future__ import annotations

import json

from puripuly_heart.config.settings import (
    DEFAULT_OPENROUTER_BROKER_BASE_URL,
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    OpenRouterCredentialSource,
    from_dict,
    load_settings,
    to_dict,
)


def test_managed_identity_settings_round_trip() -> None:
    settings = AppSettings()
    settings.managed_identity.installation_id = "01961ad7-a7c1-7000-8000-0123456789ab"
    settings.managed_identity.release_token = "release-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:00:45.000Z"
    settings.managed_identity.verified_hardware_hash = "hardware-hash-1"
    settings.managed_identity.verified_hardware_hash_salt_version = 7

    restored = from_dict(to_dict(settings))

    assert restored.managed_identity == settings.managed_identity


def test_openrouter_selected_source_round_trip() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    restored = from_dict(to_dict(settings))

    assert restored.openrouter.selected_source == OpenRouterCredentialSource.MANAGED


def test_openrouter_broker_base_url_round_trip() -> None:
    settings = AppSettings()
    settings.openrouter.broker_base_url = "https://broker.example.test"

    restored = from_dict(to_dict(settings))

    assert restored.openrouter.broker_base_url == "https://broker.example.test"


def test_load_settings_backfills_managed_identity_defaults(tmp_path) -> None:
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["settings_version"] = SETTINGS_SCHEMA_VERSION - 1
    legacy.pop("managed_identity", None)
    legacy["openrouter"].pop("selected_source", None)
    legacy["openrouter"].pop("broker_base_url", None)
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.managed_identity.installation_id == ""
    assert loaded.managed_identity.release_token is None
    assert loaded.managed_identity.release_token_expires_at is None
    assert loaded.managed_identity.verified_hardware_hash is None
    assert loaded.managed_identity.verified_hardware_hash_salt_version is None
    assert loaded.openrouter.selected_source == OpenRouterCredentialSource.NONE
    assert loaded.openrouter.broker_base_url == DEFAULT_OPENROUTER_BROKER_BASE_URL
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["managed_identity"] == {
        "installation_id": "",
        "release_token": None,
        "release_token_expires_at": None,
        "verified_hardware_hash": None,
        "verified_hardware_hash_salt_version": None,
    }
    assert persisted["openrouter"]["selected_source"] == OpenRouterCredentialSource.NONE.value
    assert persisted["openrouter"]["broker_base_url"] == DEFAULT_OPENROUTER_BROKER_BASE_URL
