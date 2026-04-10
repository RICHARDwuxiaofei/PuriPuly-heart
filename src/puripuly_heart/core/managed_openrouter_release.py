from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import InitVar, dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from puripuly_heart.config.settings import AppSettings, OpenRouterCredentialSource
from puripuly_heart.core.hardware_fingerprint import compute_hardware_hash
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.managed_identity import (
    ensure_managed_identity_bundle,
    regenerate_managed_identity_bundle,
)
from puripuly_heart.core.openrouter_credentials import (
    OPENROUTER_MANAGED_API_KEY_SECRET,
    clear_temporary_managed_release_state,
    resolve_openrouter_credentials,
)
from puripuly_heart.core.storage.secrets import SecretStore
from puripuly_heart.domain.models import Translation

MANAGED_OPENROUTER_TRIAL_MODEL = "google/gemma-4-26b-a4b-it"
MANAGED_OPENROUTER_TRIAL_BUDGET_USD = 0.07
BINDING_MISMATCH_SUBCODES = {
    "device_public_key_registered",
    "installation_binding_mismatch",
}
HardwareFingerprintProvider = Callable[[], str | Awaitable[str]]


def _default_signed_at() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _default_monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


class ManagedOpenRouterReleaseBehavior(str, Enum):
    READY = "ready"
    RETRY = "retry"
    RESTART = "restart"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterReleaseResult:
    behavior: ManagedOpenRouterReleaseBehavior
    message_key: str
    message_kwargs: Mapping[str, object] = field(default_factory=dict)
    retry_after_ms: int | None = None
    api_key: str | None = None
    local_key_available: bool = False
    pending_issue: bool = False
    single_flight_reused: bool = False


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterFingerprintSalt:
    version: int
    salt: str


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterChallengeSuccess:
    challenge: str
    challenge_expires_at: str
    fingerprint_salt: ManagedOpenRouterFingerprintSalt


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterVerifySuccess:
    release_token: str
    release_token_expires_at: str


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterIssueSuccess:
    openrouter_api_key: str
    managed_credential_ref: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterPreflightStop:
    reason: str


@dataclass(frozen=True, slots=True)
class ManagedOpenRouterReleaseError(Exception):
    code: str
    error_class: str
    message: str
    subcode: str | None = None
    retry_after_ms: int | None = None

    def __str__(self) -> str:
        return self.message or self.code


class ManagedOpenRouterReleaseClient(Protocol):
    async def challenge(
        self,
        *,
        installation_id: str,
        device_public_key: str,
        app_version: str,
    ) -> ManagedOpenRouterChallengeSuccess | ManagedOpenRouterPreflightStop: ...

    async def verify(self, request: dict[str, str]) -> ManagedOpenRouterVerifySuccess: ...

    async def issue(self, request: dict[str, object]) -> ManagedOpenRouterIssueSuccess: ...


@dataclass(slots=True)
class UnavailableManagedOpenRouterReleaseClient:
    async def challenge(
        self,
        *,
        installation_id: str,
        device_public_key: str,
        app_version: str,
    ) -> ManagedOpenRouterPreflightStop:
        _ = installation_id, device_public_key, app_version
        return ManagedOpenRouterPreflightStop(reason="unavailable")

    async def verify(self, request: dict[str, str]) -> ManagedOpenRouterVerifySuccess:
        _ = request
        raise ManagedOpenRouterReleaseError(
            code="trial_unavailable",
            error_class="retryable",
            message="managed OpenRouter release is unavailable",
        )

    async def issue(self, request: dict[str, object]) -> ManagedOpenRouterIssueSuccess:
        _ = request
        raise ManagedOpenRouterReleaseError(
            code="trial_unavailable",
            error_class="retryable",
            message="managed OpenRouter release is unavailable",
        )


@dataclass(slots=True)
class ManagedOpenRouterReleaseService:
    settings: AppSettings
    secrets: SecretStore
    client: ManagedOpenRouterReleaseClient
    persist_settings: Callable[[AppSettings], None]
    app_version: str
    raw_hardware_fingerprint_provider: HardwareFingerprintProvider | None = None
    hardware_hash_provider: InitVar[HardwareFingerprintProvider | None] = None
    signed_at_provider: Callable[[], str] = _default_signed_at
    monotonic_ms_provider: Callable[[], int] = _default_monotonic_ms
    _prepare_task: asyncio.Task[ManagedOpenRouterReleaseResult] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _issue_task: asyncio.Task[ManagedOpenRouterReleaseResult] | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _retry_after_deadline_ms: int | None = field(init=False, default=None, repr=False)
    _legacy_hardware_hash_provider: HardwareFingerprintProvider | None = field(
        init=False,
        default=None,
        repr=False,
    )

    def __post_init__(self, hardware_hash_provider: HardwareFingerprintProvider | None) -> None:
        self._legacy_hardware_hash_provider = hardware_hash_provider

    def _start_shared_task(
        self,
        attr_name: str,
        coro: Awaitable[ManagedOpenRouterReleaseResult],
    ) -> asyncio.Task[ManagedOpenRouterReleaseResult]:
        task = asyncio.create_task(coro)
        setattr(self, attr_name, task)

        def _clear(finished_task: asyncio.Task[ManagedOpenRouterReleaseResult]) -> None:
            if getattr(self, attr_name) is finished_task:
                setattr(self, attr_name, None)

        task.add_done_callback(_clear)
        return task

    async def _await_shared_task(
        self,
        task: asyncio.Task[ManagedOpenRouterReleaseResult],
        *,
        single_flight_reused: bool,
    ) -> ManagedOpenRouterReleaseResult:
        result = await asyncio.shield(task)
        if single_flight_reused:
            return replace(result, single_flight_reused=True)
        return result

    async def prepare_for_translation(self) -> ManagedOpenRouterReleaseResult:
        if self._issue_task is not None and not self._issue_task.done():
            return await self._await_shared_task(self._issue_task, single_flight_reused=True)
        if self._prepare_task is not None and not self._prepare_task.done():
            return await self._await_shared_task(self._prepare_task, single_flight_reused=True)

        task = self._start_shared_task("_prepare_task", self._run_prepare_flow())
        return await self._await_shared_task(task, single_flight_reused=False)

    async def ensure_key_for_llm_start(self) -> ManagedOpenRouterReleaseResult:
        resolution = resolve_openrouter_credentials(self.settings, secrets=self.secrets)
        if resolution.selected_source != OpenRouterCredentialSource.MANAGED:
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.STOP,
                message_key="managed_release.stop",
            )
        if resolution.api_key is not None:
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.READY,
                message_key="managed_release.ready",
                api_key=resolution.api_key,
                local_key_available=True,
            )

        if self._prepare_task is not None and not self._prepare_task.done():
            prepare_result = await self._await_shared_task(
                self._prepare_task,
                single_flight_reused=True,
            )
            if prepare_result.behavior != ManagedOpenRouterReleaseBehavior.READY:
                return prepare_result

        if self._issue_task is not None and not self._issue_task.done():
            return await self._await_shared_task(self._issue_task, single_flight_reused=True)

        if _normalize_optional_text(self.settings.managed_identity.release_token) is None:
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                message_key="managed_release.restart",
            )

        retry_result = self._result_for_retry_after_window()
        if retry_result is not None:
            return retry_result

        task = self._start_shared_task("_issue_task", self._run_issue_flow())
        return await self._await_shared_task(task, single_flight_reused=False)

    async def _run_prepare_flow(self) -> ManagedOpenRouterReleaseResult:
        resolution = resolve_openrouter_credentials(
            self.settings,
            secrets=self.secrets,
            request_intent="TRANS",
        )
        if resolution.selected_source != OpenRouterCredentialSource.MANAGED:
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.STOP,
                message_key="managed_release.stop",
            )
        if resolution.api_key is not None:
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.READY,
                message_key="managed_release.ready",
                local_key_available=True,
            )

        bundle = ensure_managed_identity_bundle(
            self.settings,
            self.secrets,
            persist_settings=self.persist_settings,
        )
        retry_result = self._result_for_retry_after_window()
        if retry_result is not None:
            return retry_result
        if _normalize_optional_text(self.settings.managed_identity.release_token) is not None:
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.READY,
                message_key="managed_release.ready",
                pending_issue=True,
            )

        try:
            challenge_response = await self.client.challenge(
                installation_id=bundle.installation_id,
                device_public_key=bundle.device_public_key,
                app_version=self.app_version,
            )
        except ManagedOpenRouterReleaseError as exc:
            return self._handle_release_error(exc)

        if isinstance(challenge_response, ManagedOpenRouterPreflightStop):
            self._clear_retry_after()
            clear_temporary_managed_release_state(self.settings)
            self.persist_settings(self.settings)
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.STOP,
                message_key=f"managed_release.{challenge_response.reason}",
            )

        try:
            hardware_hash = await self._resolve_hardware_hash(
                fingerprint_salt=challenge_response.fingerprint_salt,
            )
        except Exception:
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.STOP,
                message_key="managed_release.stop",
            )
        verify_request = bundle.sign_verify_request(
            challenge=challenge_response.challenge,
            challenge_expires_at=challenge_response.challenge_expires_at,
            hardware_hash=hardware_hash,
            app_version=self.app_version,
            signed_at=self.signed_at_provider(),
        )
        try:
            verify_response = await self.client.verify(verify_request)
        except ManagedOpenRouterReleaseError as exc:
            return self._handle_release_error(exc)

        self.settings.managed_identity.release_token = verify_response.release_token
        self.settings.managed_identity.release_token_expires_at = (
            verify_response.release_token_expires_at
        )
        self.settings.managed_identity.verified_hardware_hash = hardware_hash
        self.settings.managed_identity.verified_hardware_hash_salt_version = (
            challenge_response.fingerprint_salt.version
        )
        self.persist_settings(self.settings)
        self._clear_retry_after()
        return ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            pending_issue=True,
        )

    async def _run_issue_flow(self) -> ManagedOpenRouterReleaseResult:
        bundle = ensure_managed_identity_bundle(
            self.settings,
            self.secrets,
            persist_settings=self.persist_settings,
        )
        release_token = _normalize_optional_text(self.settings.managed_identity.release_token)
        if release_token is None:
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                message_key="managed_release.restart",
            )
        verified_hardware_hash = _normalize_optional_text(
            self.settings.managed_identity.verified_hardware_hash
        )
        verified_hardware_hash_salt_version = (
            self.settings.managed_identity.verified_hardware_hash_salt_version
        )
        if verified_hardware_hash is None or verified_hardware_hash_salt_version is None:
            clear_temporary_managed_release_state(self.settings)
            self.persist_settings(self.settings)
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                message_key="managed_release.restart",
            )
        issue_request = bundle.sign_issue_request(
            release_token=release_token,
            reason="llm_start",
            hardware_hash=verified_hardware_hash,
            budget_usd=MANAGED_OPENROUTER_TRIAL_BUDGET_USD,
            model=MANAGED_OPENROUTER_TRIAL_MODEL,
            signed_at=self.signed_at_provider(),
        )
        try:
            issue_response = await self.client.issue(issue_request)
        except ManagedOpenRouterReleaseError as exc:
            return self._handle_release_error(exc)

        self.secrets.set(OPENROUTER_MANAGED_API_KEY_SECRET, issue_response.openrouter_api_key)
        clear_temporary_managed_release_state(self.settings)
        self.persist_settings(self.settings)
        self._clear_retry_after()
        return ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.READY,
            message_key="managed_release.ready",
            api_key=issue_response.openrouter_api_key,
            local_key_available=True,
        )

    def _handle_release_error(
        self,
        error: ManagedOpenRouterReleaseError,
    ) -> ManagedOpenRouterReleaseResult:
        if error.error_class == "security_fail" and error.subcode in BINDING_MISMATCH_SUBCODES:
            try:
                regenerate_managed_identity_bundle(
                    self.settings,
                    self.secrets,
                    persist_settings=self.persist_settings,
                )
            except Exception:
                self._clear_retry_after()
                return ManagedOpenRouterReleaseResult(
                    behavior=ManagedOpenRouterReleaseBehavior.STOP,
                    message_key="managed_release.stop",
                )
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                message_key="managed_release.restart",
            )

        if (
            error.error_class == "security_fail"
            or error.code
            in {
                "challenge_expired",
                "release_token_expired",
            }
            or error.subcode == "release_token_expired"
        ):
            clear_temporary_managed_release_state(self.settings)
            self.persist_settings(self.settings)
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                message_key="managed_release.restart",
            )

        if error.error_class == "terminal":
            clear_temporary_managed_release_state(self.settings)
            self.persist_settings(self.settings)
            self._clear_retry_after()
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.STOP,
                message_key=(
                    "managed_release.not_eligible"
                    if error.code == "trial_not_eligible"
                    else "managed_release.stop"
                ),
            )

        retry_after_ms = _normalize_retry_after_ms(error.retry_after_ms)
        if retry_after_ms is not None:
            self._retry_after_deadline_ms = self.monotonic_ms_provider() + retry_after_ms
            return ManagedOpenRouterReleaseResult(
                behavior=ManagedOpenRouterReleaseBehavior.RETRY,
                message_key="managed_release.retry_after_ms",
                message_kwargs={"retry_after_ms": retry_after_ms},
                retry_after_ms=retry_after_ms,
            )

        self._clear_retry_after()
        return ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.RETRY,
            message_key="managed_release.retry",
        )

    async def _resolve_hardware_hash(
        self,
        *,
        fingerprint_salt: ManagedOpenRouterFingerprintSalt,
    ) -> str:
        if self.raw_hardware_fingerprint_provider is not None:
            raw_hardware_fingerprint = await _resolve_provider_without_blocking_event_loop(
                self.raw_hardware_fingerprint_provider
            )
            return compute_hardware_hash(
                fingerprint_salt=fingerprint_salt.salt,
                raw_fingerprint=raw_hardware_fingerprint,
            )
        if self._legacy_hardware_hash_provider is not None:
            hardware_hash = await _resolve_provider_without_blocking_event_loop(
                self._legacy_hardware_hash_provider
            )
            normalized_hardware_hash = _normalize_optional_text(hardware_hash)
            if normalized_hardware_hash is None:
                raise ValueError("hardware_hash_provider must return a non-empty string")
            return normalized_hardware_hash
        raise RuntimeError("managed hardware fingerprint provider is not configured")

    def _result_for_retry_after_window(self) -> ManagedOpenRouterReleaseResult | None:
        if self._retry_after_deadline_ms is None:
            return None
        now_ms = self.monotonic_ms_provider()
        if now_ms >= self._retry_after_deadline_ms:
            self._clear_retry_after()
            return None
        remaining_ms = self._retry_after_deadline_ms - now_ms
        return ManagedOpenRouterReleaseResult(
            behavior=ManagedOpenRouterReleaseBehavior.RETRY,
            message_key="managed_release.retry_after_ms",
            message_kwargs={"retry_after_ms": remaining_ms},
            retry_after_ms=remaining_ms,
        )

    def _clear_retry_after(self) -> None:
        self._retry_after_deadline_ms = None

    async def close(self) -> None:
        prepare_task = self._prepare_task
        issue_task = self._issue_task
        self._prepare_task = None
        self._issue_task = None

        active_tasks = [
            task for task in (prepare_task, issue_task) if task is not None and not task.done()
        ]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        close_client = getattr(self.client, "close", None)
        if callable(close_client):
            close_result = close_client()
            if inspect.isawaitable(close_result):
                await close_result


class ManagedOpenRouterDelegateFactory(Protocol):
    def __call__(self, api_key: str) -> LLMProvider: ...


@dataclass(slots=True)
class ManagedOpenRouterUserFacingError(RuntimeError):
    message_key: str
    message_kwargs: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        from puripuly_heart.ui.i18n import t

        try:
            return t(self.message_key, **dict(self.message_kwargs))
        except Exception:
            return self.message_key


@dataclass(slots=True)
class ManagedOpenRouterLLMProvider(LLMProvider):
    release_service: object
    delegate_factory: ManagedOpenRouterDelegateFactory
    on_delegate_ready: Callable[[], object] | None = None
    _delegate: LLMProvider | None = field(init=False, default=None, repr=False)
    _delegate_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _ensure_delegate(self) -> LLMProvider:
        if self._delegate is not None:
            return self._delegate

        async with self._delegate_lock:
            if self._delegate is not None:
                return self._delegate
            ensure_key = getattr(self.release_service, "ensure_key_for_llm_start")
            result = await ensure_key()
            if not isinstance(result, ManagedOpenRouterReleaseResult):
                raise RuntimeError("managed release service returned an unsupported result")
            if result.behavior != ManagedOpenRouterReleaseBehavior.READY or not result.api_key:
                raise ManagedOpenRouterUserFacingError(
                    message_key=result.message_key or "managed_release.restart",
                    message_kwargs=result.message_kwargs,
                )
            self._delegate = self.delegate_factory(result.api_key)
            if self.on_delegate_ready is not None:
                callback_result = self.on_delegate_ready()
                if inspect.isawaitable(callback_result):
                    await callback_result
            return self._delegate

    async def stream_translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        delegate = await self._ensure_delegate()
        async for snapshot in delegate.stream_translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        ):
            yield snapshot

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        delegate = await self._ensure_delegate()
        return await delegate.translate(
            utterance_id=utterance_id,
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

    async def close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()
            self._delegate = None


def _normalize_optional_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_retry_after_ms(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


async def _resolve_maybe_awaitable(value: str | Awaitable[str]) -> str:
    if inspect.isawaitable(value):
        return await value
    return value


async def _resolve_provider_without_blocking_event_loop(
    provider: HardwareFingerprintProvider,
) -> str:
    if inspect.iscoroutinefunction(provider):
        return await _resolve_maybe_awaitable(provider())
    return await _resolve_maybe_awaitable(await asyncio.to_thread(provider))
