from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from puripuly_heart.app.ports.broker_client import BrokerIssueRequest, BrokerIssueResult
from puripuly_heart.app.ports.discord_auth import DiscordAuthRequest, DiscordAuthResult
from puripuly_heart.app.ports.managed_identity import (
    ManagedIdentityPreflightRequest,
    ManagedIdentityPreflightResult,
)
from puripuly_heart.app.ports.managed_identity_state import (
    ManagedIdentitySnapshot,
    ManagedIdentityStatePort,
)
from puripuly_heart.config.llm_profiles import (
    get_openrouter_llm_profile,
    openrouter_alias_for_fields,
)
from puripuly_heart.config.settings import AppSettings, TranslationConnection
from puripuly_heart.core.discord_oauth_loopback import (
    DiscordOAuthCallbackError,
    DiscordOAuthLoopbackClosedError,
    DiscordOAuthLoopbackListener,
)
from puripuly_heart.core.hardware_fingerprint import compute_hardware_hash
from puripuly_heart.core.managed_identity import (
    ManagedIdentityBundle,
    ensure_managed_identity_bundle,
)
from puripuly_heart.core.managed_openrouter_release import (
    MANAGED_OPENROUTER_TRIAL_BUDGET_USD,
    ManagedOpenRouterDiscordStartSuccess,
    ManagedOpenRouterIssueSuccess,
    ManagedOpenRouterReleaseError,
    OpenRouterReleaseRuntimeConfig,
)
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_AUTH,
    DIAGNOSTIC_CATEGORY_SERVICE_UNAVAILABLE,
    DIAGNOSTIC_CATEGORY_TRANSACTION,
    DIAGNOSTIC_VISIBILITY_BASIC,
    SEVERITY_ERROR,
    DiagnosticCategory,
    ErrorDiagnostics,
    UserMessageRef,
)
from puripuly_heart.core.openrouter_credentials import OpenRouterCredentialRuntimeConfig
from puripuly_heart.core.runtime.oauth import OAuthRuntime
from puripuly_heart.core.storage.secrets import SecretStore

HardwareFingerprintProvider = Callable[[], str | Awaitable[str]]
DiscordOAuthListenerFactory = Callable[[], DiscordOAuthLoopbackListener]
DiscordOAuthCallbackRunner = Callable[
    [DiscordOAuthLoopbackListener, str, str],
    Awaitable[tuple[str, str]],
]


@dataclass(slots=True)
class ManagedIdentityStateAdapter:
    """Boundary adapter that exposes ``AppSettings`` managed-identity state as a
    ``ManagedIdentityStatePort``.

    Reads and writes proxy directly to ``settings.managed_identity`` so that
    mutations are visible to subsequent reads before ``persist`` is called.
    ``persist`` delegates to the supplied persistence callable, which receives
    the wrapped ``AppSettings`` instance.
    """

    _settings: AppSettings
    _persist: Callable[[AppSettings], None]

    @property
    def installation_id(self) -> str:
        return self._settings.managed_identity.installation_id

    @installation_id.setter
    def installation_id(self, value: str) -> None:
        self._settings.managed_identity.installation_id = value

    @property
    def release_token(self) -> str | None:
        return self._settings.managed_identity.release_token

    @release_token.setter
    def release_token(self, value: str | None) -> None:
        self._settings.managed_identity.release_token = value

    @property
    def release_token_expires_at(self) -> str | None:
        return self._settings.managed_identity.release_token_expires_at

    @release_token_expires_at.setter
    def release_token_expires_at(self, value: str | None) -> None:
        self._settings.managed_identity.release_token_expires_at = value

    @property
    def verified_hardware_hash(self) -> str | None:
        return self._settings.managed_identity.verified_hardware_hash

    @verified_hardware_hash.setter
    def verified_hardware_hash(self, value: str | None) -> None:
        self._settings.managed_identity.verified_hardware_hash = value

    @property
    def verified_hardware_hash_salt_version(self) -> int | None:
        return self._settings.managed_identity.verified_hardware_hash_salt_version

    @verified_hardware_hash_salt_version.setter
    def verified_hardware_hash_salt_version(self, value: int | None) -> None:
        self._settings.managed_identity.verified_hardware_hash_salt_version = value

    @property
    def active_managed_credential_ref(self) -> str | None:
        return self._settings.managed_identity.active_managed_credential_ref

    @active_managed_credential_ref.setter
    def active_managed_credential_ref(self, value: str | None) -> None:
        self._settings.managed_identity.active_managed_credential_ref = value

    @property
    def active_managed_expires_at(self) -> str | None:
        return self._settings.managed_identity.active_managed_expires_at

    @active_managed_expires_at.setter
    def active_managed_expires_at(self, value: str | None) -> None:
        self._settings.managed_identity.active_managed_expires_at = value

    @property
    def founder_letter_seen_credential_ref(self) -> str | None:
        return self._settings.managed_identity.founder_letter_seen_credential_ref

    @founder_letter_seen_credential_ref.setter
    def founder_letter_seen_credential_ref(self, value: str | None) -> None:
        self._settings.managed_identity.founder_letter_seen_credential_ref = value

    @property
    def referral_id(self) -> str | None:
        return self._settings.managed_identity.referral_id

    @referral_id.setter
    def referral_id(self, value: str | None) -> None:
        self._settings.managed_identity.referral_id = value

    @property
    def local_managed_claim_sources(self) -> tuple[str, ...]:
        return self._settings.managed_identity.local_managed_claim_sources

    @local_managed_claim_sources.setter
    def local_managed_claim_sources(self, value: tuple[str, ...]) -> None:
        self._settings.managed_identity.local_managed_claim_sources = value

    def persist(self) -> None:
        self._persist(self._settings)

    def snapshot(self) -> ManagedIdentitySnapshot:
        managed = self._settings.managed_identity
        return ManagedIdentitySnapshot(
            installation_id=managed.installation_id,
            release_token=managed.release_token,
            release_token_expires_at=managed.release_token_expires_at,
            verified_hardware_hash=managed.verified_hardware_hash,
            verified_hardware_hash_salt_version=managed.verified_hardware_hash_salt_version,
            active_managed_credential_ref=managed.active_managed_credential_ref,
            active_managed_expires_at=managed.active_managed_expires_at,
            founder_letter_seen_credential_ref=managed.founder_letter_seen_credential_ref,
            referral_id=managed.referral_id,
            local_managed_claim_sources=managed.local_managed_claim_sources,
        )

    def restore(self, snapshot: ManagedIdentitySnapshot) -> None:
        managed = self._settings.managed_identity
        managed.installation_id = snapshot.installation_id
        managed.release_token = snapshot.release_token
        managed.release_token_expires_at = snapshot.release_token_expires_at
        managed.verified_hardware_hash = snapshot.verified_hardware_hash
        managed.verified_hardware_hash_salt_version = snapshot.verified_hardware_hash_salt_version
        managed.active_managed_credential_ref = snapshot.active_managed_credential_ref
        managed.active_managed_expires_at = snapshot.active_managed_expires_at
        managed.founder_letter_seen_credential_ref = snapshot.founder_letter_seen_credential_ref
        managed.referral_id = snapshot.referral_id
        managed.local_managed_claim_sources = snapshot.local_managed_claim_sources


def build_managed_identity_state_port(
    settings: AppSettings,
    persist: Callable[[AppSettings], None],
) -> ManagedIdentityStatePort:
    """Build a ``ManagedIdentityStatePort`` adapter at the wiring boundary."""

    return ManagedIdentityStateAdapter(settings, persist)


@dataclass(slots=True)
class ManagedIdentityPreflightAdapter:
    managed_state: ManagedIdentityStatePort
    secrets: SecretStore
    _bundle: ManagedIdentityBundle | None = None

    async def preflight_managed_identity(
        self,
        request: ManagedIdentityPreflightRequest,
    ) -> ManagedIdentityPreflightResult:
        _ = request
        try:
            bundle = await self.ensure_bundle()
        except Exception:
            return ManagedIdentityPreflightResult(
                succeeded=False,
                local_public_key=None,
                local_identity_revision=None,
                message=_message("discord_auth.error.retry"),
                diagnostics=_diagnostics(
                    component="managed_identity_preflight",
                    operation="preflight_managed_identity",
                    code="managed_identity_preflight_failed",
                    category=DIAGNOSTIC_CATEGORY_AUTH,
                ),
            )
        return ManagedIdentityPreflightResult(
            succeeded=True,
            local_public_key=bundle.device_public_key,
            local_identity_revision=bundle.installation_id,
            message=None,
            diagnostics=None,
        )

    async def ensure_bundle(self) -> ManagedIdentityBundle:
        if self._bundle is None:
            self._bundle = await asyncio.to_thread(
                ensure_managed_identity_bundle,
                self.managed_state,
                self.secrets,
            )
        return self._bundle


@dataclass(slots=True)
class DiscordOAuthAuthAdapter:
    identity: ManagedIdentityPreflightAdapter
    client: object
    app_version: str
    raw_hardware_fingerprint_provider: HardwareFingerprintProvider | None
    hardware_hash_provider: HardwareFingerprintProvider | None
    oauth_runtime: OAuthRuntime
    listener_factory: DiscordOAuthListenerFactory
    callback_runner: DiscordOAuthCallbackRunner
    referral_id: str | None = None
    on_callback_received: Callable[[], None] | None = None

    async def start_discord_auth(self, request: DiscordAuthRequest) -> DiscordAuthResult:
        _ = request
        listener: DiscordOAuthLoopbackListener | None = None
        try:
            bundle = await self.identity.ensure_bundle()
            listener = self.listener_factory()
            self.oauth_runtime.attach_loopback_listener(listener, listener_name="discord-loopback")
            start_response = await self._start_discord_oauth(bundle, listener)
            if start_response.redirect_uri != listener.redirect_uri:
                return _discord_auth_failure("discord_redirect_mismatch")
            code, state = await self.callback_runner(
                listener,
                start_response.authorization_url,
                start_response.oauth_session_expires_at,
            )
            if self.on_callback_received is not None:
                with contextlib.suppress(Exception):
                    self.on_callback_received()
            hardware_hash = await self._hardware_hash(start_response)
            return DiscordAuthResult(
                succeeded=True,
                discord_user_id=None,
                message=None,
                diagnostics=None,
                authorization_code=code,
                oauth_state=state,
                redirect_uri=listener.redirect_uri,
                issue_nonce=start_response.issue_nonce,
                hardware_hash=hardware_hash,
                hardware_hash_salt_version=start_response.fingerprint_salt_version,
            )
        except ManagedOpenRouterReleaseError as exc:
            return _discord_auth_failure_from_release_error(exc)
        except (
            DiscordOAuthCallbackError,
            DiscordOAuthLoopbackClosedError,
            TimeoutError,
        ):
            return _discord_auth_failure("discord_callback_failed")
        except Exception:
            return _discord_auth_failure("discord_auth_exception")
        finally:
            if listener is not None:
                await self.oauth_runtime.close_loopback_listener(
                    listener,
                    listener_name="discord-loopback",
                )

    async def _start_discord_oauth(
        self,
        bundle: ManagedIdentityBundle,
        listener: DiscordOAuthLoopbackListener,
    ) -> ManagedOpenRouterDiscordStartSuccess:
        start = getattr(self.client, "start_discord_oauth")
        return await start(
            installation_id=bundle.installation_id,
            device_public_key=bundle.device_public_key,
            redirect_uri=listener.redirect_uri,
            app_version=self.app_version,
            referral_id=self.referral_id,
        )

    async def _hardware_hash(self, start_response: ManagedOpenRouterDiscordStartSuccess) -> str:
        if self.raw_hardware_fingerprint_provider is not None:
            raw = await _resolve_provider_without_blocking_event_loop(
                self.raw_hardware_fingerprint_provider
            )
            return compute_hardware_hash(
                fingerprint_salt=start_response.fingerprint_salt.salt,
                raw_fingerprint=raw,
            )
        if self.hardware_hash_provider is not None:
            hardware_hash = await _resolve_provider_without_blocking_event_loop(
                self.hardware_hash_provider
            )
            normalized_hardware_hash = _normalize_optional_text(hardware_hash)
            if normalized_hardware_hash is None:
                raise RuntimeError("hardware hash provider returned an invalid value")
            return normalized_hardware_hash
        raise RuntimeError("managed hardware fingerprint provider is not configured")


@dataclass(slots=True)
class DiscordManagedBrokerClientAdapter:
    identity: ManagedIdentityPreflightAdapter
    client: object
    openrouter_config: OpenRouterReleaseRuntimeConfig
    app_version: str
    signed_at_provider: Callable[[], str]
    last_issue_response: ManagedOpenRouterIssueSuccess | None = None

    async def issue_managed_connection(self, request: BrokerIssueRequest) -> BrokerIssueResult:
        missing = _missing_discord_issue_request_fields(request)
        if missing:
            return _broker_issue_failure("discord_issue_material_missing")
        try:
            bundle = await self.identity.ensure_bundle()
            issue_request = bundle.sign_discord_issue_request(
                code=request.authorization_code or "",
                state=request.oauth_state or "",
                redirect_uri=request.redirect_uri or "",
                hardware_hash=request.hardware_hash or "",
                hardware_hash_salt_version=request.hardware_hash_salt_version or 0,
                app_version=self.app_version,
                reason="llm_start",
                budget_usd=MANAGED_OPENROUTER_TRIAL_BUDGET_USD,
                model=_resolve_managed_issue_model(self.openrouter_config),
                issue_nonce=request.issue_nonce or "",
                signed_at=self.signed_at_provider(),
            )
            issue = await getattr(self.client, "issue_discord_managed_key")(issue_request)
        except ManagedOpenRouterReleaseError as exc:
            return _broker_issue_failure_from_release_error(exc)
        except Exception:
            return _broker_issue_failure("discord_issue_exception")
        self.last_issue_response = issue
        apply_discord_issue_result_to_managed_state(self.identity.managed_state, issue)
        return BrokerIssueResult(
            succeeded=True,
            broker_connection_id=issue.managed_credential_ref,
            managed_secret_key=issue.openrouter_api_key,
            remote_key_revision=issue.managed_credential_ref,
            message=None,
            diagnostics=None,
            managed_credential_ref=issue.managed_credential_ref,
            expires_at=issue.expires_at,
            openrouter_user_id=issue.openrouter_user_id,
            referral_id=issue.referral_id,
            referral_bonus_applied=issue.referral_bonus_applied,
            pass_status=issue.pass_status,
        )

    async def assert_qq_managed_identity(self, request: object) -> object:
        assert_qq = getattr(self.client, "assert_qq_managed_identity")
        return await assert_qq(request)


def apply_discord_issue_result_to_managed_state(
    managed_state: ManagedIdentityStatePort,
    issue: ManagedOpenRouterIssueSuccess,
) -> None:
    current_ref = managed_state.active_managed_credential_ref
    next_ref = (
        _normalize_optional_text(issue.managed_credential_ref)
        or current_ref
        or _normalize_optional_text(issue.expires_at)
        or managed_state.installation_id
        or "managed-entitlement"
    )
    if current_ref != next_ref:
        managed_state.founder_letter_seen_credential_ref = None
    managed_state.active_managed_credential_ref = next_ref
    managed_state.active_managed_expires_at = _normalize_optional_text(issue.expires_at)
    referral_id = _normalize_owned_referral_id(issue.referral_id)
    if referral_id is not None:
        managed_state.referral_id = referral_id
    managed_state.release_token = None
    managed_state.release_token_expires_at = None
    managed_state.verified_hardware_hash = None
    managed_state.verified_hardware_hash_salt_version = None


async def _resolve_provider_without_blocking_event_loop(
    provider: HardwareFingerprintProvider,
) -> str:
    if inspect.iscoroutinefunction(provider):
        return await _resolve_maybe_awaitable(provider())
    return await _resolve_maybe_awaitable(await asyncio.to_thread(provider))


async def _resolve_maybe_awaitable(value: str | Awaitable[str]) -> str:
    if inspect.isawaitable(value):
        resolved = await value
    else:
        resolved = value
    if not isinstance(resolved, str) or not resolved.strip():
        raise RuntimeError("hardware fingerprint provider returned an invalid value")
    return resolved


def _missing_discord_issue_request_fields(request: BrokerIssueRequest) -> bool:
    return not bool(
        request.authorization_code
        and request.oauth_state
        and request.redirect_uri
        and request.issue_nonce
        and request.hardware_hash
        and request.hardware_hash_salt_version is not None
    )


def _discord_auth_failure_from_release_error(
    error: ManagedOpenRouterReleaseError,
) -> DiscordAuthResult:
    return DiscordAuthResult(
        succeeded=False,
        discord_user_id=None,
        message=_message(_discord_message_key_for_release_error(error)),
        diagnostics=_diagnostics(
            component="discord_managed_auth",
            operation=error.operation or "discord_auth",
            code=error.code,
            category=DIAGNOSTIC_CATEGORY_AUTH,
            subcode=error.subcode,
            retry_after_ms=error.retry_after_ms,
        ),
    )


def _discord_auth_failure(code: str) -> DiscordAuthResult:
    return DiscordAuthResult(
        succeeded=False,
        discord_user_id=None,
        message=_message("discord_auth.error.retry"),
        diagnostics=_diagnostics(
            component="discord_managed_auth",
            operation="discord_auth",
            code=code,
            category=DIAGNOSTIC_CATEGORY_AUTH,
        ),
    )


def _broker_issue_failure_from_release_error(
    error: ManagedOpenRouterReleaseError,
) -> BrokerIssueResult:
    return BrokerIssueResult(
        succeeded=False,
        broker_connection_id=None,
        managed_secret_key=None,
        remote_key_revision=None,
        message=_message(_discord_message_key_for_release_error(error)),
        diagnostics=_diagnostics(
            component="managed_openrouter_broker_client",
            operation=error.operation or "discord_issue",
            code=error.code,
            category=DIAGNOSTIC_CATEGORY_SERVICE_UNAVAILABLE,
            subcode=error.subcode,
            retry_after_ms=error.retry_after_ms,
        ),
    )


def _broker_issue_failure(code: str) -> BrokerIssueResult:
    return BrokerIssueResult(
        succeeded=False,
        broker_connection_id=None,
        managed_secret_key=None,
        remote_key_revision=None,
        message=_message("discord_auth.error.retry"),
        diagnostics=_diagnostics(
            component="managed_openrouter_broker_client",
            operation="discord_issue",
            code=code,
            category=DIAGNOSTIC_CATEGORY_TRANSACTION,
        ),
    )


def _discord_message_key_for_release_error(error: ManagedOpenRouterReleaseError) -> str:
    if error.subcode == "discord_email_unverified":
        return "discord_auth.error.email_unverified"
    if error.subcode == "discord_account_too_new":
        return "discord_auth.error.account_too_new"
    if error.subcode == "discord_lifetime_used":
        return "discord_auth.error.lifetime_used"
    if error.subcode == "hardware_duplicate":
        return "discord_auth.error.hardware_duplicate"
    if error.subcode == "global_cap_reached":
        return "discord_auth.error.daily_cap"
    if error.subcode == "oauth_session_expired":
        return "discord_auth.error.expired"
    if error.code == "discord_loopback_unavailable":
        return "discord_auth.error.loopback_unavailable"
    return "discord_auth.error.retry"


def _message(key: str) -> UserMessageRef:
    return UserMessageRef(key=key, params={}, severity=SEVERITY_ERROR)


def _diagnostics(
    *,
    component: str,
    operation: str,
    code: str,
    category: DiagnosticCategory,
    subcode: str | None = None,
    retry_after_ms: int | None = None,
) -> ErrorDiagnostics:
    fields: dict[str, str | int | float | bool | None] = {"phase": operation}
    if subcode is not None:
        fields["subcode"] = subcode
    return ErrorDiagnostics(
        component=component,
        operation=operation,
        code=code,
        category=category,
        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=retry_after_ms,
        fields=fields,
    )


def _resolve_managed_issue_model(config: OpenRouterReleaseRuntimeConfig) -> str:
    selection_alias = config.selection_alias
    if selection_alias is None:
        selection_alias = openrouter_alias_for_fields(
            model=config.llm_model.value,
            source=config.selected_source.value,
        )
    profile = get_openrouter_llm_profile(
        selection_alias.value if hasattr(selection_alias, "value") else selection_alias
    )
    if profile is not None and profile.openrouter_model is not None:
        return profile.openrouter_model
    return config.llm_model.value


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_owned_referral_id(value: object) -> str | None:
    from puripuly_heart.config.settings import normalize_owned_referral_id

    return normalize_owned_referral_id(value)


def build_openrouter_credential_runtime_config(
    settings: AppSettings,
) -> OpenRouterCredentialRuntimeConfig:
    """Build a narrow OpenRouter credential runtime DTO from legacy settings."""

    return OpenRouterCredentialRuntimeConfig(
        selected_source=settings.openrouter.selected_source,
        installation_id=settings.managed_identity.installation_id,
        managed_credential_kind=_managed_credential_kind_for_settings(settings),
        active_managed_credential_ref=settings.managed_identity.active_managed_credential_ref,
        active_managed_expires_at=settings.managed_identity.active_managed_expires_at,
    )


def build_openrouter_release_runtime_config(
    settings: AppSettings,
) -> OpenRouterReleaseRuntimeConfig:
    """Build a narrow OpenRouter release runtime DTO from legacy settings."""

    return OpenRouterReleaseRuntimeConfig(
        llm_model=settings.openrouter.llm_model,
        selected_source=settings.openrouter.selected_source,
        selection_alias=settings.openrouter.selection_alias,
        managed_credential_kind=_managed_credential_kind_for_settings(settings),
    )


def _managed_credential_kind_for_settings(settings: AppSettings) -> str:
    if settings.translation.connection == TranslationConnection.MANAGED_CHINA:
        return "qq"
    return "standard"


def _managed_release_service_for_alias(
    managed_release_service: object | None,
    *,
    alias_settings: AppSettings,
) -> object | None:
    if managed_release_service is None:
        return None

    from puripuly_heart.core.managed_openrouter_release import ManagedOpenRouterReleaseService

    if not isinstance(managed_release_service, ManagedOpenRouterReleaseService):
        return managed_release_service

    desired_config = build_openrouter_release_runtime_config(alias_settings)
    if managed_release_service.openrouter_config == desired_config:
        return managed_release_service

    return ManagedOpenRouterReleaseService(
        openrouter_config=desired_config,
        managed_state=ManagedIdentityStateAdapter(
            alias_settings,
            lambda _settings: managed_release_service.managed_state.persist(),
        ),
        secrets=managed_release_service.secrets,
        client=managed_release_service.client,
        app_version=managed_release_service.app_version,
        raw_hardware_fingerprint_provider=managed_release_service.raw_hardware_fingerprint_provider,
        hardware_hash_provider=managed_release_service._legacy_hardware_hash_provider,
        signed_at_provider=managed_release_service.signed_at_provider,
        monotonic_ms_provider=managed_release_service.monotonic_ms_provider,
    )
