from __future__ import annotations

import json

import pytest

from puripuly_heart.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    AudioSettings,
    LLMProviderName,
    OSCSettings,
    QwenLLMModel,
    from_dict,
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


def test_qwen_llm_model_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS
    save_settings(path, settings)

    loaded = load_settings(path)
    assert loaded.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["qwen"]["llm_model"] == "qwen3.5-plus"


def test_load_settings_migrates_legacy_qwen_mt_flash_model(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["qwen"]["llm_model"] = "qwen-mt-flash"
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["qwen"]["llm_model"] == "qwen3.5-plus"


def test_system_prompts_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.system_prompts = {
        "gemini": "gemini prompt",
        "qwen": "qwen prompt",
    }
    settings.provider.llm = LLMProviderName.QWEN
    settings.system_prompt = "qwen prompt"
    save_settings(path, settings)

    loaded = load_settings(path)
    assert loaded.system_prompts["gemini"] == "gemini prompt"
    assert loaded.system_prompts["qwen"] == "qwen prompt"
    assert loaded.system_prompt == "qwen prompt"


def test_from_dict_uses_prompt_for_selected_provider():
    data = to_dict(AppSettings())
    data["provider"]["llm"] = "qwen"
    data["system_prompts"] = {
        "gemini": "gemini custom",
        "qwen": "qwen custom",
    }
    data["system_prompt"] = "legacy"

    loaded = from_dict(data)
    assert loaded.system_prompt == "qwen custom"
    assert loaded.system_prompts["gemini"] == "gemini custom"
    assert loaded.system_prompts["qwen"] == "qwen custom"


def test_from_dict_backfills_legacy_system_prompt_to_selected_provider():
    data = to_dict(AppSettings())
    data["provider"]["llm"] = "gemini"
    data["system_prompt"] = "legacy prompt"
    data.pop("system_prompts", None)

    loaded = from_dict(data)
    assert loaded.system_prompts["gemini"] == "legacy prompt"
    assert loaded.system_prompt == "legacy prompt"


def test_load_settings_migrates_legacy_soniox_model_and_persists(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["settings_version"] = 2
    legacy["soniox_stt"]["model"] = "stt-rt-v3"
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.settings_version == SETTINGS_SCHEMA_VERSION
    assert loaded.soniox_stt.model == "stt-rt-v4"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["soniox_stt"]["model"] == "stt-rt-v4"


def test_load_settings_migration_preserves_custom_soniox_model(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["settings_version"] = 2
    legacy["soniox_stt"]["model"] = "stt-rt-experimental"
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.settings_version == SETTINGS_SCHEMA_VERSION
    assert loaded.soniox_stt.model == "stt-rt-experimental"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["settings_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["soniox_stt"]["model"] == "stt-rt-experimental"


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
