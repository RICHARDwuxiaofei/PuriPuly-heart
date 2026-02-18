from __future__ import annotations

import json

import pytest

from puripuly_heart.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    AudioSettings,
    OSCSettings,
    load_settings,
    save_settings,
    to_dict,
)
from puripuly_heart.core.storage.secrets import EncryptedFileSecretStore, mask_secret


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    save_settings(path, settings)

    loaded = load_settings(path)
    expected = AppSettings()
    expected.languages.recent_source_languages = ["en", "zh-CN", "ja", "ko", "es", "fr"]
    expected.languages.recent_target_languages = ["en", "zh-CN", "ja", "ko", "es", "fr"]

    assert loaded == expected


def test_settings_validation_rejects_invalid_audio():
    settings = AppSettings(audio=AudioSettings(internal_sample_rate_hz=123))
    with pytest.raises(ValueError):
        settings.validate()


def test_settings_validation_rejects_invalid_osc():
    settings = AppSettings(osc=OSCSettings(ttl_s=-1))
    with pytest.raises(ValueError):
        settings.validate()


def test_load_settings_migrates_legacy_concurrency_limit_and_persists(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy.pop("settings_version", None)
    legacy["llm"]["concurrency_limit"] = 1
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.settings_version == SETTINGS_SCHEMA_VERSION
    assert loaded.llm.concurrency_limit == 2

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["llm"]["concurrency_limit"] == 2


def test_load_settings_migration_preserves_custom_concurrency_limit(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy.pop("settings_version", None)
    legacy["llm"]["concurrency_limit"] = 3
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.settings_version == SETTINGS_SCHEMA_VERSION
    assert loaded.llm.concurrency_limit == 3

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["llm"]["concurrency_limit"] == 3


def test_mask_secret():
    assert mask_secret("sk-123456") == "sk-****"
    assert mask_secret("abc", unmasked_prefix=3) == "***"


def test_encrypted_file_secret_store_roundtrip(tmp_path):
    path = tmp_path / "secrets.json"
    store = EncryptedFileSecretStore(path, passphrase="pw")
    store.set("google_api_key", "sk-SECRET")

    assert store.get("google_api_key") == "sk-SECRET"
    store.delete("google_api_key")
    assert store.get("google_api_key") is None


def test_encrypted_file_secret_store_does_not_store_plaintext(tmp_path):
    path = tmp_path / "secrets.json"
    store = EncryptedFileSecretStore(path, passphrase="pw")
    store.set("k", "sk-SECRET")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "sk-SECRET" not in json.dumps(raw)


def test_encrypted_file_secret_store_rejects_wrong_passphrase(tmp_path):
    path = tmp_path / "secrets.json"
    store = EncryptedFileSecretStore(path, passphrase="pw")
    store.set("k", "sk-SECRET")

    wrong = EncryptedFileSecretStore(path, passphrase="wrong")
    with pytest.raises(ValueError):
        wrong.get("k")
