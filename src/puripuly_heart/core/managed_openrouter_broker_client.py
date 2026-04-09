from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterChallengeSuccess,
    ManagedOpenRouterFingerprintSalt,
    ManagedOpenRouterIssueSuccess,
    ManagedOpenRouterPreflightStop,
    ManagedOpenRouterReleaseError,
    ManagedOpenRouterVerifySuccess,
)

RETRYABLE_ERROR_CODE = "trial_unavailable"
RETRYABLE_ERROR_CLASS = "retryable"
PUBLIC_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "rate_limited",
        "challenge_expired",
        "challenge_invalid",
        "issuance_suspended",
        "trial_unavailable",
        "trial_not_eligible",
        "internal_error",
    }
)
PUBLIC_ERROR_CLASSES = frozenset({"retryable", "terminal", "security_fail"})


@dataclass(slots=True)
class HttpManagedOpenRouterBrokerClient:
    base_url: str
    timeout: float = 10.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url)

    async def challenge(
        self,
        *,
        installation_id: str,
        device_public_key: str,
        app_version: str,
    ) -> ManagedOpenRouterChallengeSuccess | ManagedOpenRouterPreflightStop:
        payload = await self._post_json(
            path="/v1/trial/challenge",
            request_body={
                "installation_id": installation_id,
                "device_public_key": device_public_key,
                "app_version": app_version,
            },
            operation="challenge",
        )
        try:
            return ManagedOpenRouterChallengeSuccess(
                challenge=_require_text(payload, "challenge"),
                challenge_expires_at=_require_text(payload, "challenge_expires_at"),
                fingerprint_salt=_parse_fingerprint_salt(payload),
            )
        except ValueError as exc:
            raise _retryable_error(
                "challenge", f"broker returned malformed payload: {exc}"
            ) from exc

    async def verify(self, request: dict[str, str]) -> ManagedOpenRouterVerifySuccess:
        payload = await self._post_json(
            path="/v1/trial/challenge/verify",
            request_body=request,
            operation="verify",
        )
        try:
            return ManagedOpenRouterVerifySuccess(
                release_token=_require_text(payload, "release_token"),
                release_token_expires_at=_require_text(payload, "release_token_expires_at"),
            )
        except ValueError as exc:
            raise _retryable_error("verify", f"broker returned malformed payload: {exc}") from exc

    async def issue(self, request: dict[str, object]) -> ManagedOpenRouterIssueSuccess:
        payload = await self._post_json(
            path="/v1/providers/openrouter/issue",
            request_body=request,
            operation="issue",
        )
        try:
            return ManagedOpenRouterIssueSuccess(
                openrouter_api_key=_require_text(payload, "openrouter_api_key"),
                managed_credential_ref=_require_text(payload, "managed_credential_ref"),
                expires_at=_require_text(payload, "expires_at"),
            )
        except ValueError as exc:
            raise _retryable_error("issue", f"broker returned malformed payload: {exc}") from exc

    async def close(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    async def _post_json(
        self,
        *,
        path: str,
        request_body: Mapping[str, object],
        operation: str,
    ) -> Mapping[str, object]:
        client = await self._get_http_client()
        try:
            response = await client.post(path, json=dict(request_body))
        except httpx.TimeoutException as exc:
            raise _retryable_error(operation, f"broker request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise _retryable_error(operation, f"broker transport failure: {exc}") from exc
        except httpx.HTTPError as exc:
            raise _retryable_error(operation, f"broker request failed: {exc}") from exc

        if response.is_error:
            raise _parse_error_response(response, operation=operation)

        return _parse_json_mapping(response, operation=operation)

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is None:
                normalized_base_url = self.base_url.strip().rstrip("/")
                self._client = httpx.AsyncClient(
                    base_url=normalized_base_url,
                    timeout=self.timeout,
                    transport=self.transport,
                )
            return self._client


def _parse_error_response(
    response: httpx.Response, *, operation: str
) -> ManagedOpenRouterReleaseError:
    payload = _parse_json_mapping(response, operation=operation)
    raw_error = payload.get("error")
    if not isinstance(raw_error, Mapping):
        return _retryable_error(
            operation,
            f"broker returned an unexpected error payload (status={response.status_code})",
        )

    try:
        return ManagedOpenRouterReleaseError(
            code=_require_public_error_code(raw_error, "code"),
            error_class=_require_public_error_class(raw_error, "class"),
            subcode=_require_optional_text(raw_error, "subcode"),
            retry_after_ms=_require_optional_int(raw_error, "retry_after_ms"),
            message=_require_text(raw_error, "message"),
        )
    except ValueError as exc:
        return _retryable_error(operation, f"broker returned malformed error payload: {exc}")


def _parse_json_mapping(response: httpx.Response, *, operation: str) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise _retryable_error(operation, "broker returned malformed JSON") from exc
    if not isinstance(payload, Mapping):
        raise _retryable_error(operation, "broker returned a non-object JSON payload")
    return payload


def _parse_fingerprint_salt(payload: Mapping[str, object]) -> ManagedOpenRouterFingerprintSalt:
    raw_fingerprint_salt = payload.get("fingerprint_salt")
    if not isinstance(raw_fingerprint_salt, Mapping):
        raise _retryable_error("challenge", "broker returned malformed fingerprint_salt payload")
    return ManagedOpenRouterFingerprintSalt(
        version=_require_int(raw_fingerprint_salt, "version"),
        salt=_require_text(raw_fingerprint_salt, "salt"),
    )


def _require_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_public_error_code(payload: Mapping[str, object], key: str) -> str:
    value = _require_text(payload, key)
    if value not in PUBLIC_ERROR_CODES:
        raise ValueError(f"{key} must be a supported public error code")
    return value


def _require_public_error_class(payload: Mapping[str, object], key: str) -> str:
    value = _require_text(payload, key)
    if value not in PUBLIC_ERROR_CLASSES:
        raise ValueError(f"{key} must be a supported public error class")
    return value


def _require_optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _require_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _require_optional_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _retryable_error(operation: str, detail: str) -> ManagedOpenRouterReleaseError:
    return ManagedOpenRouterReleaseError(
        code=RETRYABLE_ERROR_CODE,
        error_class=RETRYABLE_ERROR_CLASS,
        message=f"managed OpenRouter broker {operation} failed: {detail}",
    )


def _normalize_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("broker base_url must be a non-empty string")
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.path not in {"", "/"}:
        raise ValueError("broker base_url must not include a path prefix")
    return normalized
