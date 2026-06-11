from __future__ import annotations

import pytest

from puripuly_heart.core.error_messages import sanitize_legacy_raw_user_visible_error_text


@pytest.mark.parametrize("alias", ["raw_payload", "provider_payload", "raw_body"])
def test_legacy_raw_user_visible_error_text_redacts_provider_raw_payload_aliases(
    alias: str,
) -> None:
    raw = (
        f"Provider failed {alias}="
        "{'error': {'message': 'bad'}, 'token': 'provider-secret-alias'}"
        "; status=500"
    )

    sanitized = sanitize_legacy_raw_user_visible_error_text(raw)

    assert sanitized is not None
    assert "Provider failed" in sanitized
    assert "[provider-response-body-redacted]" in sanitized
    assert alias not in sanitized
    assert "provider-secret-alias" not in sanitized
    assert "'error':" not in sanitized


@pytest.mark.parametrize(
    "alias",
    ["broker_raw_eligibility_message", "raw_broker_eligibility_message"],
)
def test_legacy_raw_user_visible_error_text_redacts_broker_raw_message_aliases(
    alias: str,
) -> None:
    raw = (
        f"Broker failed {alias}="
        "{'eligible': false, 'tail': 'broker-sensitive-tail', 'secret': 'broker-secret-alias'}"
        "; retry later"
    )

    sanitized = sanitize_legacy_raw_user_visible_error_text(raw)

    assert sanitized is not None
    assert "Broker failed" in sanitized
    assert "[broker-raw-message-redacted]" in sanitized
    assert alias not in sanitized
    assert "broker-sensitive-tail" not in sanitized
    assert "broker-secret-alias" not in sanitized


def test_legacy_raw_user_visible_error_text_redacts_provider_payload_secret_and_traceback() -> None:
    raw = (
        "OpenRouter request failed provider_response_body="
        "{'error':'bad','token':'provider-secret-123'}\n"
        "Traceback (most recent call last):\n"
        '  File "provider.py", line 42, in translate\n'
        "RuntimeError: api_key=sk-provider-secret-456"
    )

    sanitized = sanitize_legacy_raw_user_visible_error_text(raw)

    assert sanitized is not None
    assert "OpenRouter request failed" in sanitized
    assert "provider-secret-123" not in sanitized
    assert "sk-provider-secret-456" not in sanitized
    assert "provider_response_body" not in sanitized
    assert "Traceback" not in sanitized
    assert 'File "provider.py"' not in sanitized
    assert "[redacted]" in sanitized


def test_legacy_raw_user_visible_error_text_keeps_plain_legacy_message_useful() -> None:
    assert sanitize_legacy_raw_user_visible_error_text("General failure") == "General failure"


def test_legacy_raw_user_visible_error_text_redacts_nested_provider_payload_tail() -> None:
    raw = (
        "Provider failed provider_response_body="
        "{error:{message:bad},tail:still raw provider-secret-789}"
    )

    sanitized = sanitize_legacy_raw_user_visible_error_text(raw)

    assert sanitized is not None
    assert "Provider failed" in sanitized
    assert "[provider-response-body-redacted]" in sanitized
    assert "provider_response_body" not in sanitized
    assert "tail:still raw" not in sanitized
    assert "provider-secret-789" not in sanitized


def test_legacy_raw_user_visible_error_text_redacts_bearer_authorization_assignment() -> None:
    sanitized = sanitize_legacy_raw_user_visible_error_text(
        "OpenRouter request failed authorization=Bearer provider-token-456"
    )

    assert sanitized == "OpenRouter request failed authorization=[redacted]"
    assert "provider-token-456" not in sanitized


def test_legacy_raw_user_visible_error_text_returns_none_for_blank_payload() -> None:
    assert sanitize_legacy_raw_user_visible_error_text(" \n \t ") is None
