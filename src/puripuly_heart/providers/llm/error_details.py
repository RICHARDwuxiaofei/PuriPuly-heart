from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from puripuly_heart.core.error_messages import sanitize_legacy_raw_user_visible_error_text

PROVIDER_ERROR_DETAIL_MAX_LENGTH = 256
_UNKNOWN_ERROR = "unknown error"


class ProviderErrorResponse(Protocol):
    text: str

    def json(self) -> object: ...


def extract_provider_error_detail(
    response: ProviderErrorResponse,
    *,
    sensitive_values: Iterable[str] = (),
    max_length: int = PROVIDER_ERROR_DETAIL_MAX_LENGTH,
) -> str:
    data = _json_or_none(response)
    for candidate in _json_detail_candidates(data):
        detail = _safe_detail(candidate, sensitive_values=sensitive_values, max_length=max_length)
        if detail:
            return detail

    detail = _safe_detail(response.text, sensitive_values=sensitive_values, max_length=max_length)
    return detail or _UNKNOWN_ERROR


def _json_or_none(response: ProviderErrorResponse) -> object | None:
    try:
        return response.json()
    except Exception:
        return None


def _json_detail_candidates(data: object) -> tuple[object, object, object]:
    if not isinstance(data, dict):
        return (None, None, None)

    error = data.get("error")
    nested_message = error.get("message") if isinstance(error, dict) else None
    return (data.get("message"), nested_message, error)


def _safe_detail(
    value: object,
    *,
    sensitive_values: Iterable[str],
    max_length: int,
) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    text = _redact_sensitive_values(text, sensitive_values)
    text = sanitize_legacy_raw_user_visible_error_text(text)
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def _redact_sensitive_values(text: str, sensitive_values: Iterable[str]) -> str:
    redacted = text
    for value in sensitive_values:
        if not value or len(value) < 8:
            continue
        redacted = redacted.replace(value, "[redacted]")
    return redacted


__all__ = ["PROVIDER_ERROR_DETAIL_MAX_LENGTH", "extract_provider_error_detail"]
