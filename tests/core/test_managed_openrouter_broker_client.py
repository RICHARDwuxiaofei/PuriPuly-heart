from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterChallengeSuccess,
    ManagedOpenRouterFingerprintSalt,
    ManagedOpenRouterIssueSuccess,
    ManagedOpenRouterReleaseError,
    ManagedOpenRouterVerifySuccess,
)


class TrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)

    async def aclose(self) -> None:
        self.closed = True


def _build_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = "https://broker.example.test",
) -> tuple[object, TrackingTransport]:
    from puripuly_heart.core.managed_openrouter_broker_client import (
        HttpManagedOpenRouterBrokerClient,
    )

    transport = TrackingTransport(handler)
    return (
        HttpManagedOpenRouterBrokerClient(base_url=base_url, transport=transport, timeout=1.0),
        transport,
    )


@pytest.mark.asyncio
async def test_challenge_parses_fingerprint_salt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/trial/challenge"
        assert json.loads(request.content) == {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "app_version": "2.0.0",
        }
        return httpx.Response(
            200,
            json={
                "challenge": "challenge-123",
                "challenge_expires_at": "2026-04-10T06:05:00.000Z",
                "fingerprint_salt": {
                    "version": 7,
                    "salt": "fingerprint-salt-123",
                },
                "managed_state": {
                    "lifecycle": "none",
                    "managed_availability": True,
                },
                "current_entitlement": None,
            },
        )

    client, _transport = _build_client(handler)

    result = await client.challenge(
        installation_id="install-123",
        device_public_key="device-public-key-123",
        app_version="2.0.0",
    )

    assert result == ManagedOpenRouterChallengeSuccess(
        challenge="challenge-123",
        challenge_expires_at="2026-04-10T06:05:00.000Z",
        fingerprint_salt=ManagedOpenRouterFingerprintSalt(
            version=7,
            salt="fingerprint-salt-123",
        ),
    )
    await client.close()


@pytest.mark.asyncio
async def test_verify_parses_success_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/trial/challenge/verify"
        assert json.loads(request.content) == {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "challenge": "challenge-123",
            "challenge_expires_at": "2026-04-10T06:05:00.000Z",
            "hardware_hash": "hardware-hash-123",
            "app_version": "2.0.0",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
        return httpx.Response(
            200,
            json={
                "release_token": "release-token-123",
                "release_token_expires_at": "2026-04-10T06:15:00.000Z",
                "managed_state": {
                    "lifecycle": "pending_release",
                    "managed_availability": True,
                },
            },
        )

    client, _transport = _build_client(handler)

    result = await client.verify(
        {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "challenge": "challenge-123",
            "challenge_expires_at": "2026-04-10T06:05:00.000Z",
            "hardware_hash": "hardware-hash-123",
            "app_version": "2.0.0",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
    )

    assert result == ManagedOpenRouterVerifySuccess(
        release_token="release-token-123",
        release_token_expires_at="2026-04-10T06:15:00.000Z",
    )
    await client.close()


@pytest.mark.asyncio
async def test_issue_parses_success_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/providers/openrouter/issue"
        assert json.loads(request.content) == {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "release_token": "release-token-123",
            "reason": "llm_start",
            "budget_usd": 0.07,
            "model": "google/gemma-4-26b-a4b-it",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
        return httpx.Response(
            200,
            json={
                "openrouter_api_key": "managed-openrouter-api-key",
                "managed_credential_ref": "managed-credential-ref-123",
                "expires_at": "2026-10-10T06:00:00.000Z",
                "managed_state": {
                    "lifecycle": "active",
                    "managed_availability": True,
                },
                "budget_usd": 0.07,
                "model": "google/gemma-4-26b-a4b-it",
            },
        )

    client, _transport = _build_client(handler)

    result = await client.issue(
        {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "release_token": "release-token-123",
            "reason": "llm_start",
            "budget_usd": 0.07,
            "model": "google/gemma-4-26b-a4b-it",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
    )

    assert result == ManagedOpenRouterIssueSuccess(
        openrouter_api_key="managed-openrouter-api-key",
        managed_credential_ref="managed-credential-ref-123",
        expires_at="2026-10-10T06:00:00.000Z",
    )
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "openrouter_api_key": "managed-openrouter-api-key",
            "expires_at": "2026-10-10T06:00:00.000Z",
        },
        {
            "openrouter_api_key": "managed-openrouter-api-key",
            "managed_credential_ref": "managed-credential-ref-123",
        },
        {
            "openrouter_api_key": "managed-openrouter-api-key",
            "managed_credential_ref": None,
            "expires_at": "2026-10-10T06:00:00.000Z",
        },
        {
            "openrouter_api_key": "managed-openrouter-api-key",
            "managed_credential_ref": "managed-credential-ref-123",
            "expires_at": None,
        },
    ],
)
async def test_issue_accepts_missing_or_null_optional_success_fields(
    payload: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **payload,
                "managed_state": {
                    "lifecycle": "active",
                    "managed_availability": True,
                },
                "budget_usd": 0.07,
                "model": "google/gemma-4-26b-a4b-it",
            },
        )

    client, _transport = _build_client(handler)

    result = await client.issue(
        {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "release_token": "release-token-123",
            "reason": "llm_start",
            "budget_usd": 0.07,
            "model": "google/gemma-4-26b-a4b-it",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
    )

    assert result == ManagedOpenRouterIssueSuccess(
        openrouter_api_key="managed-openrouter-api-key",
        managed_credential_ref=(
            payload.get("managed_credential_ref") if "managed_credential_ref" in payload else None
        ),
        expires_at=payload.get("expires_at") if "expires_at" in payload else None,
    )
    await client.close()


@pytest.mark.asyncio
async def test_nested_broker_error_envelope_becomes_release_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "trial_unavailable",
                    "class": "retryable",
                    "subcode": "broker_backoff",
                    "retry_after_ms": 9000,
                    "message": "broker is temporarily unavailable",
                },
                "managed_state": {
                    "lifecycle": "none",
                    "managed_availability": True,
                },
                "current_entitlement": None,
            },
        )

    client, _transport = _build_client(handler)

    with pytest.raises(ManagedOpenRouterReleaseError) as exc_info:
        await client.issue(
            {
                "installation_id": "install-123",
                "device_public_key": "device-public-key-123",
                "release_token": "release-token-123",
                "reason": "llm_start",
                "budget_usd": 0.07,
                "model": "google/gemma-4-26b-a4b-it",
                "signed_at": "2026-04-10T06:00:45.000Z",
                "signature": "signature-123",
            }
        )

    assert exc_info.value == ManagedOpenRouterReleaseError(
        code="trial_unavailable",
        error_class="retryable",
        subcode="broker_backoff",
        retry_after_ms=9000,
        message="broker is temporarily unavailable",
    )
    await client.close()


@pytest.mark.asyncio
async def test_issue_preserves_managed_key_unrecoverable_subcode() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "error": {
                    "code": "internal_error",
                    "class": "terminal",
                    "subcode": "managed_key_unrecoverable",
                    "retry_after_ms": None,
                    "message": "managed key could not be recovered",
                }
            },
        )

    client, _transport = _build_client(handler)

    with pytest.raises(ManagedOpenRouterReleaseError) as exc_info:
        await client.issue(
            {
                "installation_id": "install-123",
                "device_public_key": "device-public-key-123",
                "release_token": "release-token-123",
                "reason": "llm_start",
                "budget_usd": 0.07,
                "model": "google/gemma-4-26b-a4b-it",
                "signed_at": "2026-04-10T06:00:45.000Z",
                "signature": "signature-123",
            }
        )

    assert exc_info.value == ManagedOpenRouterReleaseError(
        code="internal_error",
        error_class="terminal",
        subcode="managed_key_unrecoverable",
        retry_after_ms=None,
        message="managed key could not be recovered",
    )
    await client.close()


@pytest.mark.asyncio
async def test_issue_preserves_release_token_expired_error_code() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "release_token_expired",
                    "class": "retryable",
                    "subcode": None,
                    "retry_after_ms": None,
                    "message": "release token expired",
                }
            },
        )

    client, _transport = _build_client(handler)

    with pytest.raises(ManagedOpenRouterReleaseError) as exc_info:
        await client.issue(
            {
                "installation_id": "install-123",
                "device_public_key": "device-public-key-123",
                "release_token": "release-token-123",
                "reason": "llm_start",
                "budget_usd": 0.07,
                "model": "google/gemma-4-26b-a4b-it",
                "signed_at": "2026-04-10T06:00:45.000Z",
                "signature": "signature-123",
            }
        )

    assert exc_info.value == ManagedOpenRouterReleaseError(
        code="release_token_expired",
        error_class="retryable",
        subcode=None,
        retry_after_ms=None,
        message="release token expired",
    )
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_payload",
    [
        {
            "code": "surprise_error",
            "class": "retryable",
            "subcode": None,
            "retry_after_ms": None,
            "message": "unexpected code",
        },
        {
            "code": "trial_unavailable",
            "class": "surprise_class",
            "subcode": None,
            "retry_after_ms": None,
            "message": "unexpected class",
        },
    ],
)
async def test_unknown_broker_error_vocabulary_becomes_retryable_malformed_error(
    error_payload: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": error_payload})

    client, _transport = _build_client(handler)

    with pytest.raises(ManagedOpenRouterReleaseError) as exc_info:
        await client.verify(
            {
                "installation_id": "install-123",
                "device_public_key": "device-public-key-123",
                "challenge": "challenge-123",
                "challenge_expires_at": "2026-04-10T06:05:00.000Z",
                "hardware_hash": "hardware-hash-123",
                "app_version": "2.0.0",
                "signed_at": "2026-04-10T06:00:45.000Z",
                "signature": "signature-123",
            }
        )

    assert exc_info.value.code == "trial_unavailable"
    assert exc_info.value.error_class == "retryable"
    assert "malformed error payload" in exc_info.value.message
    await client.close()


def test_rejects_broker_base_url_with_path_prefix() -> None:
    with pytest.raises(ValueError, match="path prefix"):
        _build_client(
            lambda _request: httpx.Response(200, json={}),
            base_url="https://broker.example.test/prefix",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "handler"),
    [
        (
            "timeout",
            lambda request: (_ for _ in ()).throw(
                httpx.ReadTimeout("request timed out", request=request)
            ),
        ),
        (
            "network",
            lambda request: (_ for _ in ()).throw(
                httpx.ConnectError("network unavailable", request=request)
            ),
        ),
        (
            "malformed_json",
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"{",
            ),
        ),
    ],
)
async def test_transport_failures_and_malformed_json_become_retryable_release_errors(
    label: str,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    client, _transport = _build_client(handler)

    with pytest.raises(ManagedOpenRouterReleaseError) as exc_info:
        await client.verify(
            {
                "installation_id": "install-123",
                "device_public_key": "device-public-key-123",
                "challenge": "challenge-123",
                "challenge_expires_at": "2026-04-10T06:05:00.000Z",
                "hardware_hash": "hardware-hash-123",
                "app_version": "2.0.0",
                "signed_at": "2026-04-10T06:00:45.000Z",
                "signature": "signature-123",
            }
        )

    assert exc_info.value.code == "trial_unavailable"
    assert exc_info.value.error_class == "retryable"
    assert exc_info.value.retry_after_ms is None
    assert isinstance(exc_info.value.message, str)
    assert exc_info.value.message
    await client.close()


@pytest.mark.asyncio
async def test_close_closes_underlying_client() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "release_token": "release-token-123",
                "release_token_expires_at": "2026-04-10T06:15:00.000Z",
            },
        )

    client, transport = _build_client(handler)

    await client.verify(
        {
            "installation_id": "install-123",
            "device_public_key": "device-public-key-123",
            "challenge": "challenge-123",
            "challenge_expires_at": "2026-04-10T06:05:00.000Z",
            "hardware_hash": "hardware-hash-123",
            "app_version": "2.0.0",
            "signed_at": "2026-04-10T06:00:45.000Z",
            "signature": "signature-123",
        }
    )

    internal_client = client._client
    assert internal_client is not None
    assert internal_client.is_closed is False
    assert transport.closed is False

    await client.close()

    assert internal_client.is_closed is True
    assert transport.closed is True
    assert client._client is None
