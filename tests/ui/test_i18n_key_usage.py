from __future__ import annotations

import json
from pathlib import Path

from puripuly_heart.ui import i18n as i18n_module
from puripuly_heart.ui.i18n import available_locales, source_label

REPO_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = REPO_ROOT / "src" / "puripuly_heart" / "data" / "i18n"
RUNTIME_SOURCE_DIR = REPO_ROOT / "src" / "puripuly_heart"

DYNAMIC_I18N_PREFIXES = (
    "language.",
    "locale.",
    "provider.",
    "region.",
    "settings.subtab.",
    "settings.overlay.calibration.anchor.",
    "settings.overlay.calibration.text_scale.",
    "settings.overlay.failure.",
    "settings.overlay.status.",
    "settings.peer_translation.status.",
    "logs.mode.",
    "settings.translation_model.",
)


def _load_bundles() -> dict[str, dict[str, str]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(I18N_DIR.glob("*.json"))
    }


def _runtime_python_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(RUNTIME_SOURCE_DIR.rglob("*.py"))
    )


def test_i18n_bundles_share_the_same_keys() -> None:
    bundles = _load_bundles()
    assert "en" in bundles

    expected_keys = set(bundles["en"])
    mismatches = {
        locale: {
            "missing": sorted(expected_keys - set(bundle)),
            "extra": sorted(set(bundle) - expected_keys),
        }
        for locale, bundle in bundles.items()
        if set(bundle) != expected_keys
    }

    assert mismatches == {}


def test_available_locales_use_product_display_order() -> None:
    assert available_locales() == ("en", "ko", "zh-CN", "ja")


def test_clipboard_source_and_setting_keys_are_localized() -> None:
    bundles = _load_bundles()
    required_keys = {
        "source.clipboard",
        "settings.clipboard_auto_translate",
        "settings.clipboard_auto_translate.on",
        "settings.clipboard_auto_translate.off",
    }

    for locale, bundle in bundles.items():
        missing = sorted(required_keys - set(bundle))
        assert missing == [], locale
        for key in required_keys:
            assert bundle[key].strip()
            assert bundle[key] != key

    previous_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ko")
        assert source_label("Clipboard") == "클립보드"
    finally:
        i18n_module.set_locale(previous_locale)


def test_logs_conversation_keys_are_localized() -> None:
    bundles = _load_bundles()
    required_keys = {
        "logs.conversation.show",
        "logs.conversation.hide",
        "logs.conversation.empty",
    }

    for locale, bundle in bundles.items():
        missing = sorted(required_keys - set(bundle))
        assert missing == [], locale
        for key in required_keys:
            assert bundle[key].strip()
            assert bundle[key] != key

    assert bundles["ko"]["logs.conversation.show"] == "대화록 보기"


def test_local_llm_keys_are_localized() -> None:
    bundles = _load_bundles()
    required_keys = {
        "provider.local_llms",
        "provider.local_llm",
        "settings.translation_model.local_llm.description",
        "settings.translation_connection.ollama",
        "settings.translation_connection.ollama.description",
        "settings.local_llm.connection",
        "settings.local_llm.base_url",
        "settings.local_llm.base_url.invalid",
        "settings.local_llm.model",
        "settings.local_llm.model.required",
        "settings.local_llm.api_key",
        "settings.local_llm.api_key.description",
        "settings.local_llm.api_key.save_failed",
        "settings.local_llm.extra_body",
        "settings.local_llm.extra_body.description",
        "settings.local_llm.extra_body.invalid_json",
        "settings.local_llm.extra_body.must_be_object",
        "settings.local_llm.extra_body.reserved_key",
        "settings.local_llm.extra_body.sensitive_key",
        "settings.local_llm.extra_body.not_serializable",
    }

    for locale in ("en", "ko", "ja", "zh-CN"):
        bundle = bundles[locale]
        missing = sorted(required_keys - set(bundle))
        assert missing == [], locale
        for key in required_keys:
            if key in {
                "settings.translation_model.local_llm.description",
                "settings.local_llm.api_key.description",
            }:
                assert bundle[key] == ""
                continue
            assert bundle[key].strip()
            assert bundle[key] != key

    assert bundles["en"]["settings.translation_connection.ollama"] == "OpenAI-compatible API"
    assert bundles["ko"]["settings.translation_connection.ollama"] == "OpenAI 호환 API"
    assert bundles["ko"]["settings.local_llm.connection"] == "OpenAI 호환 LLM 서버"
    assert bundles["ko"]["settings.local_llm.base_url"] == "Base URL"
    expected_model_copy = {
        "en": ("Model ID", "Enter a model ID."),
        "ko": ("모델 ID", "모델 ID를 입력해 주세요."),
        "ja": ("モデルID", "モデルIDを入力してください。"),
        "zh-CN": ("模型 ID", "请输入模型 ID。"),
    }
    for locale, (model_label, required_label) in expected_model_copy.items():
        assert bundles[locale]["settings.local_llm.model"] == model_label
        assert bundles[locale]["settings.local_llm.model.required"] == required_label
    assert bundles["ko"]["settings.local_llm.api_key"] == "서버 API 키 (선택)"
    assert bundles["ko"]["settings.local_llm.api_key.description"] == ""
    assert bundles["ko"]["settings.local_llm.extra_body.description"].startswith("낮은 지연시간")
    assert "서버 API 키" in bundles["ko"]["settings.local_llm.extra_body.sensitive_key"]


def test_managed_key_card_keys_are_localized() -> None:
    bundles = _load_bundles()
    required_keys = {
        "settings.managed_key.title",
        "settings.managed_key.referral_id.label",
        "settings.managed_key.referral_id.empty",
        "settings.managed_key.referral_id.pending_helper",
        "settings.managed_key.referral_id.helper",
        "settings.managed_key.referral_id.copy_tooltip",
        "settings.managed_key.referral_id.copy_success",
        "settings.managed_key.invite_progress.label",
    }

    for locale, bundle in bundles.items():
        missing = sorted(required_keys - set(bundle))
        assert missing == [], locale
        for key in required_keys:
            assert bundle[key].strip()
            assert bundle[key] != key

    assert bundles["en"]["settings.managed_key.title"] == "Managed Key"
    assert bundles["en"]["settings.managed_key.referral_id.label"] == "Talk Together Pass ID"
    assert bundles["en"]["settings.managed_key.referral_id.empty"] == "—"
    assert bundles["en"]["settings.managed_key.referral_id.copy_success"] == "Pass ID copied."
    assert bundles["ko"]["settings.managed_key.title"] == "매니지드 키"
    ko = bundles["ko"]
    assert ko["settings.managed_key.referral_id.copy_tooltip"] == "Pass ID 복사"
    assert ko["settings.managed_key.referral_id.copy_success"] == "Pass ID를 복사했어요."
    assert ko["settings.managed_key.referral_id.helper"] == (
        "친구에게 Pass ID를 공유해 함께 추가 사용량을 받을 수 있어요."
    )
    assert ko["settings.managed_key.invite_progress.label"] == "친구 초대"

    for locale_name, bundle in bundles.items():
        for key in (
            "settings.managed_key.referral_id.label",
            "settings.managed_key.referral_id.helper",
            "settings.managed_key.referral_id.copy_tooltip",
            "settings.managed_key.referral_id.copy_success",
            "settings.managed_key.invite_progress.label",
            "discord_auth.referral_id.label",
            "discord_auth.referral_reward_applied",
        ):
            value = bundle[key]
            assert "Referral ID" not in value, (locale_name, key, value)
            assert "Referral reward" not in value, (locale_name, key, value)


def test_i18n_bundles_do_not_keep_unused_runtime_keys() -> None:
    bundles = _load_bundles()
    all_keys = sorted(set().union(*(bundle.keys() for bundle in bundles.values())))
    runtime_source = _runtime_python_source()

    unused_keys = [
        key
        for key in all_keys
        if key not in runtime_source and not key.startswith(DYNAMIC_I18N_PREFIXES)
    ]

    assert unused_keys == []
