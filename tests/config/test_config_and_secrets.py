from __future__ import annotations

import json
import re

import pytest

from puripuly_heart.config.settings import (
    SETTINGS_SCHEMA_VERSION,
    AppSettings,
    AudioSettings,
    GeminiLLMModel,
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


def test_gemini_llm_model_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_31_FLASH_LITE
    save_settings(path, settings)

    loaded = load_settings(path)
    assert loaded.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["gemini"]["llm_model"] == "gemini-3.1-flash-lite-preview"


def test_load_settings_migrates_legacy_qwen_mt_flash_model(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["qwen"]["llm_model"] = "qwen-mt-flash"
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["qwen"]["llm_model"] == "qwen3.5-plus"


def test_load_settings_migrates_legacy_invalid_gemini_model(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy["gemini"]["llm_model"] = "gemini-legacy-foo"
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)
    assert loaded.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["gemini"]["llm_model"] == "gemini-3.1-flash-lite-preview"


def test_from_dict_defaults_missing_gemini_model_to_flash_lite():
    data = to_dict(AppSettings())
    data["gemini"] = {}

    loaded = from_dict(data)
    assert loaded.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE


def test_app_settings_defaults_vrc_mic_sync_to_off():
    settings = AppSettings()

    assert settings.osc.vrc_mic_intercept is False


def test_from_dict_defaults_missing_vrc_mic_sync_to_off():
    data = to_dict(AppSettings())
    data.setdefault("osc", {}).pop("vrc_mic_intercept", None)

    loaded = from_dict(data)

    assert loaded.osc.vrc_mic_intercept is False


def test_overlay_display_preferences_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.ui.show_overlay_translation = False
    settings.ui.show_overlay_peer_original = False
    save_settings(path, settings)

    loaded = load_settings(path)

    assert loaded.ui.show_overlay_translation is False
    assert loaded.ui.show_overlay_peer_original is False

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["ui"]["show_overlay_translation"] is False
    assert persisted["ui"]["show_overlay_peer_original"] is False


def test_from_dict_defaults_missing_overlay_display_preferences_to_true():
    data = to_dict(AppSettings())
    data.setdefault("ui", {}).pop("show_overlay_translation", None)
    data["ui"].pop("show_overlay_peer_original", None)

    loaded = from_dict(data)

    assert loaded.ui.show_overlay_translation is True
    assert loaded.ui.show_overlay_peer_original is True


def test_load_settings_backfills_missing_overlay_display_preferences(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy.setdefault("ui", {}).pop("show_overlay_translation", None)
    legacy["ui"].pop("show_overlay_peer_original", None)
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.ui.show_overlay_translation is True
    assert loaded.ui.show_overlay_peer_original is True

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["ui"]["show_overlay_translation"] is True
    assert persisted["ui"]["show_overlay_peer_original"] is True


def test_load_settings_backfills_overlay_display_preferences_when_ui_section_missing(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy.pop("ui", None)
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.ui.show_overlay_translation is True
    assert loaded.ui.show_overlay_peer_original is True

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["ui"]["show_overlay_translation"] is True
    assert persisted["ui"]["show_overlay_peer_original"] is True


def test_stt_custom_vocabulary_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {
        "ko": [" Puripuly ", "VRChat", "Puripuly", ""],
        "en": ["OSC", " Soniox "],
    }

    save_settings(path, settings)

    loaded = load_settings(path)

    assert loaded.stt.custom_vocabulary_enabled is True
    assert loaded.stt.custom_terms == {
        "ko": ["Puripuly", "VRChat"],
        "en": ["OSC", "Soniox"],
    }

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["stt"]["custom_vocabulary_enabled"] is True
    assert persisted["stt"]["custom_terms"] == {
        "ko": ["Puripuly", "VRChat"],
        "en": ["OSC", "Soniox"],
    }


def test_stt_custom_vocabulary_missing_keys_default():
    data = to_dict(AppSettings())
    data.setdefault("stt", {}).pop("custom_vocabulary_enabled", None)
    data["stt"].pop("custom_terms", None)

    loaded = from_dict(data)

    assert loaded.stt.custom_vocabulary_enabled is True
    assert loaded.stt.custom_terms == {
        "ko": ["아이리", "시나노"],
        "en": ["airi", "shinano"],
        "zh-CN": ["airi", "shinano"],
    }


def test_load_settings_backfills_seeded_custom_vocabulary_defaults(tmp_path):
    path = tmp_path / "settings.json"
    legacy = to_dict(AppSettings())
    legacy.setdefault("stt", {}).pop("custom_vocabulary_enabled", None)
    legacy["stt"].pop("custom_terms", None)
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.stt.custom_vocabulary_enabled is True
    assert loaded.stt.custom_terms == {
        "ko": ["아이리", "시나노"],
        "en": ["airi", "shinano"],
        "zh-CN": ["airi", "shinano"],
    }

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["stt"]["custom_vocabulary_enabled"] is True
    assert persisted["stt"]["custom_terms"] == {
        "ko": ["아이리", "시나노"],
        "en": ["airi", "shinano"],
        "zh-CN": ["airi", "shinano"],
    }


@pytest.mark.parametrize(
    ("custom_terms", "message"),
    [
        (["Puripuly"], "custom_terms must be a dict[str, list[str]]"),
        ({1: ["Puripuly"]}, "custom_terms keys must be strings"),
        ({"ko": "Puripuly"}, "custom_terms values must be lists of strings"),
        ({"ko": ["Puripuly", 1]}, "custom_terms values must be lists of strings"),
    ],
)
def test_stt_custom_vocabulary_rejects_malformed_shapes(custom_terms, message):
    data = to_dict(AppSettings())
    data.setdefault("stt", {})["custom_terms"] = custom_terms

    with pytest.raises(ValueError, match=re.escape(message)):
        from_dict(data)


def test_stt_custom_vocabulary_preserves_unrelated_language_buckets(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.stt.custom_terms = {
        "ko": ["Puripuly"],
        "zh-CN": ["Qwen"],
        "en": ["OSC"],
    }

    save_settings(path, settings)

    loaded = load_settings(path)
    loaded.stt.custom_terms["ko"] = ["Puripuly", "VRChat"]
    save_settings(path, loaded)

    reloaded = load_settings(path)

    assert reloaded.stt.custom_terms == {
        "ko": ["Puripuly", "VRChat"],
        "zh-CN": ["Qwen"],
        "en": ["OSC"],
    }


def test_stt_custom_vocabulary_roundtrip_caps_terms_to_100(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": [f"term-{i:03d}" for i in range(120)]}

    save_settings(path, settings)

    loaded = load_settings(path)

    assert len(loaded.stt.custom_terms["ko"]) == 100
    assert loaded.stt.custom_terms["ko"][0] == "term-000"
    assert loaded.stt.custom_terms["ko"][-1] == "term-099"


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
