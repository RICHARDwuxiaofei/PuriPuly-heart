from __future__ import annotations

import json
import re
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
    "discord_auth.referral_id.label",
    "discord_auth.referral_id.helper",
    "discord_auth.referral_reward_applied",
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
        "discord_auth.body": "PuriPuly gives new users free managed usage.\nComplete Discord verification to receive your Managed Key and start translating.",
        "discord_auth.success": "Discord verification is complete.",
    },
    "ko": {
        "discord_auth.body": "PuriPuly는 신규 사용자에게 무료 Managed 사용량을 제공해요.\nDiscord 인증을 완료하면 Managed Key가 발급되고 바로 번역을 시작할 수 있어요.",
        "discord_auth.success": "Discord 인증이 완료되었어요.",
    },
    "ja": {
        "discord_auth.body": "PuriPulyでは新規ユーザー向けに無料のManaged利用枠をご用意しています。\nDiscord認証を完了すると、Managed Keyが発行されてすぐに翻訳を始められます。",
        "discord_auth.success": "Discord認証が完了しました。",
    },
    "zh-CN": {
        "discord_auth.body": "PuriPuly 会为新用户提供免费的 Managed 使用额度。\n完成 Discord 认证后，会发放 Managed Key，你就可以开始翻译。",
        "discord_auth.success": "Discord 认证已完成。",
    },
}

_FORBIDDEN_DISCORD_AUTH_BODY_PATTERNS = {
    "numeric quantities": re.compile(r"\d"),
    "currency or dollar amounts": re.compile(r"(?:\$|USD|usd|dollars?|달러|원|円|엔|美元|美金)"),
    "estimated utterance-count wording": re.compile(r"(?:utterances?|발화|発話|发言|次|回)"),
}

_EXPECTED_REFERRAL_STRINGS = {
    "en": {
        "discord_auth.referral_id.label": "Referral ID",
        "discord_auth.referral_id.helper": "Enter a friend's Referral ID if you have one.",
        "discord_auth.referral_reward_applied": "Referral reward applied.",
    },
    "ko": {
        "discord_auth.referral_id.label": "Referral ID",
        "discord_auth.referral_id.helper": "친구에게 받은 Referral ID가 있으면 입력해 주세요.",
        "discord_auth.referral_reward_applied": "Referral 보상이 적용되었어요.",
    },
    "ja": {
        "discord_auth.referral_id.label": "Referral ID",
        "discord_auth.referral_id.helper": "友だちから受け取った Referral ID があれば入力してください。",
        "discord_auth.referral_reward_applied": "Referral 報酬が適用されました。",
    },
    "zh-CN": {
        "discord_auth.referral_id.label": "Referral ID",
        "discord_auth.referral_id.helper": "如果你有朋友给你的 Referral ID，可以在这里输入。",
        "discord_auth.referral_reward_applied": "Referral 奖励已应用。",
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


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_i18n_keys_exist_and_are_not_empty(locale: str) -> None:
    bundle = _load_bundle(locale)

    missing = [key for key in REQUIRED_DISCORD_AUTH_KEYS if key not in bundle]
    empty = [key for key in REQUIRED_DISCORD_AUTH_KEYS if bundle.get(key) == ""]

    assert missing == []
    assert empty == []


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_i18n_uses_planned_title_body_and_success_copy(
    locale: str,
) -> None:
    bundle = _load_bundle(locale)

    for key, expected_value in _EXPECTED_EXACT_STRINGS[locale].items():
        assert bundle[key] == expected_value


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_body_omits_reward_amounts_and_estimated_counts(locale: str) -> None:
    body = _load_bundle(locale)["discord_auth.body"]

    violations = {
        label: pattern.pattern
        for label, pattern in _FORBIDDEN_DISCORD_AUTH_BODY_PATTERNS.items()
        if pattern.search(body)
    }

    assert violations == {}


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN", "ja"])
def test_discord_auth_referral_i18n_uses_planned_copy(locale: str) -> None:
    bundle = _load_bundle(locale)

    for key, expected_value in _EXPECTED_REFERRAL_STRINGS[locale].items():
        assert bundle[key] == expected_value
