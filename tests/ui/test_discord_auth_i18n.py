from __future__ import annotations

import json
from pathlib import Path

import pytest

REQUIRED_DISCORD_AUTH_KEYS = [
    "discord_auth.body",
    "discord_auth.continue",
    "discord_auth.close",
    "discord_auth.reopen_browser",
    "discord_auth.cancel",
    "discord_auth.waiting_body",
    "discord_auth.callback_received_body",
    "discord_auth.success",
    "discord_auth.error.email_unverified",
    "discord_auth.error.account_too_new",
    "discord_auth.error.lifetime_used",
    "discord_auth.error.hardware_duplicate",
    "discord_auth.error.daily_cap",
    "discord_auth.error.expired",
    "discord_auth.error.loopback_unavailable",
    "discord_auth.error.retry",
    "debug_preview.discord_auth",
]


_EXPECTED_EXACT_STRINGS = {
    "en": {
        "discord_auth.body": "PuriPuly gives new users a free usage allowance.\nThat's about 600–700 translated utterances.\nYou'll receive it right after Discord verification.",
        "discord_auth.success": "Discord verification is complete.",
    },
    "ko": {
        "discord_auth.body": "PuriPuly는 신규 사용자에게 무료 사용량을 제공해요.\n발화 기준 약 600~700회를 번역할 수 있어요.\nDiscord 인증 후 바로 발급돼요.",
        "discord_auth.success": "Discord 인증이 완료되었어요.",
    },
    "zh-CN": {
        "discord_auth.body": "PuriPuly 会为新用户提供免费使用额度。\n按发言计算，可翻译约 600–700 次。\n完成 Discord 认证后会立即发放。",
        "discord_auth.success": "Discord 认证已完成。",
    },
}


def _load_bundle(locale: str) -> dict[str, str]:
    i18n_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "puripuly_heart"
        / "data"
        / "i18n"
        / f"{locale}.json"
    )
    return json.loads(i18n_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_discord_auth_i18n_keys_exist_and_are_not_empty(locale: str) -> None:
    bundle = _load_bundle(locale)

    missing = [key for key in REQUIRED_DISCORD_AUTH_KEYS if key not in bundle]
    empty = [key for key in REQUIRED_DISCORD_AUTH_KEYS if bundle.get(key) == ""]

    assert missing == []
    assert empty == []


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_discord_auth_i18n_uses_planned_title_body_and_success_copy(
    locale: str,
) -> None:
    bundle = _load_bundle(locale)

    for key, expected_value in _EXPECTED_EXACT_STRINGS[locale].items():
        assert bundle[key] == expected_value
