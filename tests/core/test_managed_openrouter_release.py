from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from puripuly_heart.config.settings import AppSettings, OpenRouterCredentialSource
from puripuly_heart.core.managed_identity import ensure_managed_identity_bundle
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterChallengeSuccess,
    ManagedOpenRouterIssueSuccess,
    ManagedOpenRouterLLMProvider,
    ManagedOpenRouterPreflightStop,
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseError,
    ManagedOpenRouterReleaseResult,
    ManagedOpenRouterReleaseService,
    ManagedOpenRouterVerifySuccess,
)
from puripuly_heart.core.openrouter_credentials import OPENROUTER_MANAGED_API_KEY_SECRET
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from puripuly_heart.domain.models import Translation


@dataclass
class FakeManagedReleaseClient:
    challenge_result: object | None = None
    verify_result: object | None = None
    issue_result: object | None = None
    challenge_gate: asyncio.Event | None = None
    calls: list[tuple[str, dict[str, object]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def challenge(
        self,
        *,
        installation_id: str,
        device_public_key: str,
        app_version: str,
    ):
        self.calls.append(
            (
                "challenge",
                {
                    "installation_id": installation_id,
                    "device_public_key": device_public_key,
                    "app_version": app_version,
                },
            )
        )
        if self.challenge_gate is not None:
            await self.challenge_gate.wait()
        result = self.challenge_result
        if isinstance(result, Exception):
            raise result
        return result

    async def verify(self, request: dict[str, str]):
        self.calls.append(("verify", dict(request)))
        result = self.verify_result
        if isinstance(result, Exception):
            raise result
        return result

    async def issue(self, request: dict[str, object]):
        self.calls.append(("issue", dict(request)))
        result = self.issue_result
        if isinstance(result, Exception):
            raise result
        return result


def _make_service(
    *,
    client: FakeManagedReleaseClient,
    settings: AppSettings | None = None,
    secrets: InMemorySecretStore | None = None,
    persist_calls: list[tuple[str | None, str | None]] | None = None,
) -> tuple[ManagedOpenRouterReleaseService, AppSettings, InMemorySecretStore]:
    resolved_settings = settings or AppSettings()
    resolved_settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    resolved_secrets = secrets or InMemorySecretStore()
    tracked_persist_calls = persist_calls if persist_calls is not None else []

    def persist(updated: AppSettings) -> None:
        tracked_persist_calls.append(
            (
                updated.managed_identity.installation_id,
                updated.managed_identity.release_token,
            )
        )

    service = ManagedOpenRouterReleaseService(
        settings=resolved_settings,
        secrets=resolved_secrets,
        client=client,
        persist_settings=persist,
        hardware_hash_provider=lambda: "hardware-hash-test",
        app_version="2.0.0",
        signed_at_provider=lambda: "2026-04-08T06:00:45.000Z",
        monotonic_ms_provider=lambda: 1_000,
    )
    return service, resolved_settings, resolved_secrets


@pytest.mark.asyncio
async def test_prepare_for_translation_short_circuits_when_managed_key_exists() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    secrets = InMemorySecretStore()
    secrets.set(OPENROUTER_MANAGED_API_KEY_SECRET, "managed-key")
    client = FakeManagedReleaseClient()
    service, _, _ = _make_service(client=client, settings=settings, secrets=secrets)

    result = await service.prepare_for_translation()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.READY
    assert result.local_key_available is True
    assert result.pending_issue is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_prepare_for_translation_runs_challenge_then_verify_and_persists_release_token() -> (
    None
):
    client = FakeManagedReleaseClient(
        challenge_result=ManagedOpenRouterChallengeSuccess(
            challenge="challenge-1",
            challenge_expires_at="2026-04-08T06:05:00.000Z",
        ),
        verify_result=ManagedOpenRouterVerifySuccess(
            release_token="release-token-1",
            release_token_expires_at="2026-04-08T06:15:00.000Z",
        ),
    )
    persist_calls: list[tuple[str | None, str | None]] = []
    service, settings, _ = _make_service(client=client, persist_calls=persist_calls)

    result = await service.prepare_for_translation()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.READY
    assert result.local_key_available is False
    assert result.pending_issue is True
    assert [name for name, _payload in client.calls] == ["challenge", "verify"]
    verify_payload = client.calls[1][1]
    assert verify_payload["challenge"] == "challenge-1"
    assert verify_payload["hardware_hash"] == "hardware-hash-test"
    assert verify_payload["app_version"] == "2.0.0"
    assert settings.managed_identity.installation_id
    assert settings.managed_identity.release_token == "release-token-1"
    assert settings.managed_identity.release_token_expires_at == "2026-04-08T06:15:00.000Z"
    assert len(persist_calls) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["not_eligible", "unavailable"])
async def test_prepare_for_translation_stops_early_on_preflight_stop(reason: str) -> None:
    client = FakeManagedReleaseClient(
        challenge_result=ManagedOpenRouterPreflightStop(reason=reason)
    )
    service, settings, secrets = _make_service(client=client)

    result = await service.prepare_for_translation()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.STOP
    assert result.message_key == f"managed_release.{reason}"
    assert settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert settings.managed_identity.release_token is None
    assert secrets.get(OPENROUTER_MANAGED_API_KEY_SECRET) is None
    assert [name for name, _payload in client.calls] == ["challenge"]


@pytest.mark.asyncio
async def test_prepare_for_translation_reuses_single_flight_for_repeated_trans_attempts() -> None:
    gate = asyncio.Event()
    client = FakeManagedReleaseClient(
        challenge_result=ManagedOpenRouterChallengeSuccess(
            challenge="challenge-1",
            challenge_expires_at="2026-04-08T06:05:00.000Z",
        ),
        verify_result=ManagedOpenRouterVerifySuccess(
            release_token="release-token-1",
            release_token_expires_at="2026-04-08T06:15:00.000Z",
        ),
        challenge_gate=gate,
    )
    service, _, _ = _make_service(client=client)

    first_task = asyncio.create_task(service.prepare_for_translation())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(service.prepare_for_translation())
    await asyncio.sleep(0)
    gate.set()

    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert [name for name, _payload in client.calls] == ["challenge", "verify"]
    assert first_result.behavior == ManagedOpenRouterReleaseBehavior.READY
    assert second_result.behavior == ManagedOpenRouterReleaseBehavior.READY
    assert sorted([first_result.single_flight_reused, second_result.single_flight_reused]) == [
        False,
        True,
    ]


@pytest.mark.asyncio
async def test_issue_honors_retry_after_without_starting_parallel_retries() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    secrets = InMemorySecretStore()
    ensure_managed_identity_bundle(settings, secrets, persist_settings=lambda _updated: None)
    settings.managed_identity.release_token = "release-token-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:15:00.000Z"
    client = FakeManagedReleaseClient(
        issue_result=ManagedOpenRouterReleaseError(
            code="issuance_suspended",
            error_class="retryable",
            message="new entitlement issuance is temporarily suspended",
            retry_after_ms=9_000,
        )
    )
    monotonic_now = {"value": 1_000}

    service = ManagedOpenRouterReleaseService(
        settings=settings,
        secrets=secrets,
        client=client,
        persist_settings=lambda _updated: None,
        hardware_hash_provider=lambda: "hardware-hash-test",
        app_version="2.0.0",
        signed_at_provider=lambda: "2026-04-08T06:00:45.000Z",
        monotonic_ms_provider=lambda: monotonic_now["value"],
    )

    first = await service.ensure_key_for_llm_start()
    second = await service.ensure_key_for_llm_start()

    assert first.behavior == ManagedOpenRouterReleaseBehavior.RETRY
    assert first.retry_after_ms == 9_000
    assert second.behavior == ManagedOpenRouterReleaseBehavior.RETRY
    assert second.retry_after_ms == 9_000
    assert [name for name, _payload in client.calls] == ["issue"]


@pytest.mark.asyncio
async def test_prepare_for_translation_honors_retry_after_while_pending_release_exists() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    secrets = InMemorySecretStore()
    ensure_managed_identity_bundle(settings, secrets, persist_settings=lambda _updated: None)
    settings.managed_identity.release_token = "release-token-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:15:00.000Z"
    client = FakeManagedReleaseClient(
        issue_result=ManagedOpenRouterReleaseError(
            code="issuance_suspended",
            error_class="retryable",
            message="new entitlement issuance is temporarily suspended",
            retry_after_ms=9_000,
        )
    )
    service = ManagedOpenRouterReleaseService(
        settings=settings,
        secrets=secrets,
        client=client,
        persist_settings=lambda _updated: None,
        hardware_hash_provider=lambda: "hardware-hash-test",
        app_version="2.0.0",
        signed_at_provider=lambda: "2026-04-08T06:00:45.000Z",
        monotonic_ms_provider=lambda: 1_000,
    )

    issue_result = await service.ensure_key_for_llm_start()
    prepare_result = await service.prepare_for_translation()

    assert issue_result.behavior == ManagedOpenRouterReleaseBehavior.RETRY
    assert issue_result.retry_after_ms == 9_000
    assert prepare_result.behavior == ManagedOpenRouterReleaseBehavior.RETRY
    assert prepare_result.retry_after_ms == 9_000
    assert [name for name, _payload in client.calls] == ["issue"]


@pytest.mark.asyncio
async def test_issue_restart_clears_release_state_without_switching_sources() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    secrets = InMemorySecretStore()
    ensure_managed_identity_bundle(settings, secrets, persist_settings=lambda _updated: None)
    settings.managed_identity.release_token = "release-token-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:15:00.000Z"
    client = FakeManagedReleaseClient(
        issue_result=ManagedOpenRouterReleaseError(
            code="challenge_invalid",
            error_class="security_fail",
            subcode="signature_mismatch",
            message="signature mismatch",
        )
    )
    service, _, _ = _make_service(client=client, settings=settings, secrets=secrets)

    result = await service.ensure_key_for_llm_start()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.RESTART
    assert settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert settings.managed_identity.release_token is None
    assert settings.managed_identity.release_token_expires_at is None


@pytest.mark.asyncio
async def test_issue_release_token_expired_restarts_and_clears_state() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    secrets = InMemorySecretStore()
    ensure_managed_identity_bundle(settings, secrets, persist_settings=lambda _updated: None)
    settings.managed_identity.release_token = "release-token-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:15:00.000Z"
    client = FakeManagedReleaseClient(
        issue_result=ManagedOpenRouterReleaseError(
            code="release_token_expired",
            error_class="retryable",
            message="release token expired",
        )
    )
    service, _, _ = _make_service(client=client, settings=settings, secrets=secrets)

    result = await service.ensure_key_for_llm_start()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.RESTART
    assert settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert settings.managed_identity.release_token is None
    assert settings.managed_identity.release_token_expires_at is None


@pytest.mark.asyncio
async def test_issue_restarts_when_identity_bundle_regenerates_before_issue() -> None:
    settings = AppSettings()
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.managed_identity.installation_id = "018f1f56-9f2d-7abc-9def-1234567890ab"
    settings.managed_identity.release_token = "release-token-1"
    settings.managed_identity.release_token_expires_at = "2026-04-08T06:15:00.000Z"
    client = FakeManagedReleaseClient(
        issue_result=ManagedOpenRouterIssueSuccess(openrouter_api_key="managed-key")
    )
    service = ManagedOpenRouterReleaseService(
        settings=settings,
        secrets=InMemorySecretStore(),
        client=client,
        persist_settings=lambda _updated: None,
        hardware_hash_provider=lambda: "hardware-hash-test",
        app_version="2.0.0",
        signed_at_provider=lambda: "2026-04-08T06:00:45.000Z",
        monotonic_ms_provider=lambda: 1_000,
    )

    result = await service.ensure_key_for_llm_start()

    assert result.behavior == ManagedOpenRouterReleaseBehavior.RESTART
    assert client.calls == []


@pytest.mark.asyncio
async def test_prepare_single_flight_survives_waiter_cancellation() -> None:
    gate = asyncio.Event()
    client = FakeManagedReleaseClient(
        challenge_result=ManagedOpenRouterChallengeSuccess(
            challenge="challenge-1",
            challenge_expires_at="2026-04-08T06:05:00.000Z",
        ),
        verify_result=ManagedOpenRouterVerifySuccess(
            release_token="release-token-1",
            release_token_expires_at="2026-04-08T06:15:00.000Z",
        ),
        challenge_gate=gate,
    )
    service, _, _ = _make_service(client=client)

    first_task = asyncio.create_task(service.prepare_for_translation())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(service.prepare_for_translation())
    await asyncio.sleep(0)
    first_task.cancel()
    await asyncio.sleep(0)
    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await first_task
    second_result = await second_task

    assert second_result.behavior == ManagedOpenRouterReleaseBehavior.READY
    assert [name for name, _payload in client.calls] == ["challenge", "verify"]


@pytest.mark.asyncio
async def test_close_cancels_in_flight_prepare_task() -> None:
    gate = asyncio.Event()
    client = FakeManagedReleaseClient(
        challenge_result=ManagedOpenRouterChallengeSuccess(
            challenge="challenge-1",
            challenge_expires_at="2026-04-08T06:05:00.000Z",
        ),
        verify_result=ManagedOpenRouterVerifySuccess(
            release_token="release-token-1",
            release_token_expires_at="2026-04-08T06:15:00.000Z",
        ),
        challenge_gate=gate,
    )
    service, _, _ = _make_service(client=client)

    task = asyncio.create_task(service.prepare_for_translation())
    await asyncio.sleep(0)
    await service.close()

    with pytest.raises(asyncio.CancelledError):
        await task


@dataclass
class FakeIssueService:
    results: list[ManagedOpenRouterReleaseResult]
    ensure_calls: list[str]

    def __init__(self, *results: ManagedOpenRouterReleaseResult) -> None:
        self.results = list(results)
        self.ensure_calls = []

    async def ensure_key_for_llm_start(self) -> ManagedOpenRouterReleaseResult:
        self.ensure_calls.append("llm_start")
        return self.results.pop(0)


@dataclass
class FakeDelegateProvider:
    translate_calls: list[dict[str, object]]

    def __init__(self) -> None:
        self.translate_calls = []

    async def translate(self, **kwargs: Any) -> Translation:
        self.translate_calls.append(dict(kwargs))
        return Translation(kwargs["utterance_id"], text="translated")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_managed_openrouter_provider_issues_on_first_llm_start_only() -> None:
    service = FakeIssueService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        )
    )
    delegate = FakeDelegateProvider()
    created_keys: list[str] = []
    provider = ManagedOpenRouterLLMProvider(
        release_service=service,
        delegate_factory=lambda api_key: created_keys.append(api_key) or delegate,
    )

    first = await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="prompt",
        source_language="ko",
        target_language="en",
    )
    second = await provider.translate(
        utterance_id=uuid4(),
        text="again",
        system_prompt="prompt",
        source_language="ko",
        target_language="en",
    )

    assert first.text == "translated"
    assert second.text == "translated"
    assert service.ensure_calls == ["llm_start"]
    assert created_keys == ["managed-key"]
    assert len(delegate.translate_calls) == 2


@pytest.mark.asyncio
async def test_managed_openrouter_provider_notifies_when_delegate_becomes_ready() -> None:
    service = FakeIssueService(
        ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key="managed-key",
            local_key_available=True,
            pending_issue=False,
        )
    )
    delegate = FakeDelegateProvider()
    ready_calls: list[str] = []
    provider = ManagedOpenRouterLLMProvider(
        release_service=service,
        delegate_factory=lambda _api_key: delegate,
        on_delegate_ready=lambda: ready_calls.append("ready"),
    )

    await provider.translate(
        utterance_id=uuid4(),
        text="hello",
        system_prompt="prompt",
        source_language="ko",
        target_language="en",
    )
    await provider.translate(
        utterance_id=uuid4(),
        text="again",
        system_prompt="prompt",
        source_language="ko",
        target_language="en",
    )

    assert ready_calls == ["ready"]
