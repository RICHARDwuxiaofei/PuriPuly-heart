from __future__ import annotations

import pytest

from puripuly_heart.core.language import (
    get_all_language_options,
    get_deepgram_language,
    get_language_info,
    get_llm_language_name,
    get_qwen_asr_language,
    get_soniox_language_hints,
    get_stt_compatibility_warning,
    is_deepgram_supported,
    is_qwen_asr_supported,
    is_soniox_supported,
    is_supported_language,
)


def test_get_language_info_handles_exact_and_regional_codes() -> None:
    assert get_language_info("ko").name == "Korean"
    assert get_language_info("ko-KR").code == "ko"
    assert get_language_info("zh-CN").name == "Chinese (Simplified)"
    assert get_language_info("xx") is None


def test_language_helpers_fallback_for_unknown() -> None:
    assert get_deepgram_language("xx") == "en"
    assert get_llm_language_name("xx") == "English"
    assert get_qwen_asr_language("xx") == "en"
    assert get_soniox_language_hints("xx") == ["en"]


def test_qwen_asr_language_normalization() -> None:
    assert get_qwen_asr_language("zh-TW") == "zh"
    assert get_qwen_asr_language("ko-KR") == "ko"


def test_get_all_language_options_sorted_by_name() -> None:
    options = list(get_all_language_options())
    names = [name for _, name in options]
    assert names == sorted(names)
    assert ("ko", "Korean") in options


def test_supported_language_checks() -> None:
    assert is_supported_language("en") is True
    assert is_supported_language("xx") is False
    assert is_deepgram_supported("en") is True
    assert is_deepgram_supported("ar") is False
    assert is_qwen_asr_supported("ar") is True
    assert is_soniox_supported("ja") is True


def test_stt_compatibility_warning_variants() -> None:
    warning = get_stt_compatibility_warning("ar", "deepgram")
    assert warning is not None
    assert warning.key == "warning.deepgram_suggest_qwen"
    assert warning.language_code == "ar"

    warning = get_stt_compatibility_warning("bg", "qwen_asr")
    assert warning is not None
    assert warning.key == "warning.qwen_suggest_deepgram"
    assert warning.language_code == "bg"

    warning = get_stt_compatibility_warning("xx", "deepgram")
    assert warning is not None
    assert warning.key == "warning.deepgram_not_supported"

    warning = get_stt_compatibility_warning("xx", "qwen_asr")
    assert warning is not None
    assert warning.key == "warning.qwen_not_supported"

    warning = get_stt_compatibility_warning("xx", "soniox")
    assert warning is not None
    assert warning.key == "warning.soniox_not_supported"
    assert warning.language_code == "xx"

    assert get_stt_compatibility_warning("en", "deepgram") is None


@pytest.mark.parametrize("code", ["en", "ko", "zh-CN"])
def test_get_language_info_returns_supported(code: str) -> None:
    info = get_language_info(code)
    assert info is not None
    assert info.code
    assert info.name
