from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from puripuly_heart.app.adapters.openrouter_pkce_production import (
    ProductionCanonicalSettingsRepository,
)
from puripuly_heart.app.ports.managed_authentication_application import (
    ManagedAuthenticationBrowserPort,
    ManagedAuthenticationPresentation,
)
from puripuly_heart.app.services.managed_auth_claims import (
    MANAGED_AUTH_CLAIM_SOURCE_DISCORD,
    MANAGED_AUTH_CLAIM_SOURCE_QQ,
    OPENROUTER_MANAGED_USER_ID_MAX_LENGTH,
    OPENROUTER_MANAGED_USER_ID_SECRET,
    OPENROUTER_MANAGED_USER_INSTALLATION_ID_SECRET,
    ManagedAuthClaimGuard,
    normalize_managed_claim_sources,
)
from puripuly_heart.app.services.managed_authentication_application import (
    ManagedAuthenticationApplication,
    managed_authentication_presentation,
)
from puripuly_heart.app.services.managed_canonical_transaction import (
    ManagedAckRequest,
    ManagedAckResult,
    ManagedCanonicalTransactionCoordinator,
    ManagedClaimInput,
    ManagedClaimResult,
    ManagedCredentialCandidate,
    ManagedPendingAckRecovery,
    ManagedTransactionRequest,
    ManagedTransactionStage,
    ack_delivered_secret_key,
    ack_delivery_confirmation_matches,
)
from puripuly_heart.app.services.managed_key_delivery_ack import (
    ManagedKeyDeliveryAckService,
    secret_key_for_ack_source,
)
from puripuly_heart.app.services.qq_managed_auth import OPENROUTER_MANAGED_QQ_API_KEY_SECRET
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseDiagnostics,
    ManagedOpenRouterReleaseResult,
)
from puripuly_heart.core.openrouter_credentials import OPENROUTER_MANAGED_API_KEY_SECRET

_MANAGED_CLAIM_SOURCE_RELEASE_TOKEN = "release_token"


class ProductionManagedAuthenticationBrowser(ManagedAuthenticationBrowserPort):
    def __init__(self, oauth_runtime) -> None:  # noqa: ANN001
        self._oauth_runtime = oauth_runtime
        self._authorization_url: str | None = None

    @property
    def available(self) -> bool:
        return self._authorization_url is not None

    def set_authorization_url(self, url: str | None) -> None:
        self._authorization_url = url

    async def reopen(self) -> bool:
        return bool(self._authorization_url and webbrowser.open(self._authorization_url))

    async def cancel(self) -> None:
        await self._oauth_runtime.cancel_auth_task("managed-discord")
        await self._oauth_runtime.cancel_auth_task("managed-qq")
        self._authorization_url = None


@dataclass(slots=True)
class ProductionManagedClaimOwner:
    runtime_host: object
    secret_store: object
    browser: ProductionManagedAuthenticationBrowser
    on_browser_available: object

    async def claim(self, request: ManagedTransactionRequest) -> ManagedClaimResult:
        release = await self.runtime_host.resolve_managed_release_service()
        if release is None or getattr(release, "client", None) is None:
            return ManagedClaimResult("failed", detail_code="managed_release_unavailable")
        if request.claim_source == _MANAGED_CLAIM_SOURCE_RELEASE_TOKEN:
            return await self._claim_release_token(release)
        secret_store = self.secret_store() if callable(self.secret_store) else self.secret_store
        guard = ManagedAuthClaimGuard(release.managed_state, secret_store)
        conflict = await guard.preflight_read_only(request.claim_source)
        if conflict is not None:
            return ManagedClaimResult(
                "conflict",
                detail_code=_transaction_detail(conflict, "managed_claim_conflict"),
            )
        if request.claim_source == MANAGED_AUTH_CLAIM_SOURCE_QQ:
            return await self._claim_qq(release, request)
        if request.claim_source == MANAGED_AUTH_CLAIM_SOURCE_DISCORD:
            return await self._claim_discord(release, request)
        return ManagedClaimResult("failed", detail_code="managed_claim_source_invalid")

    async def _claim_release_token(self, release):  # noqa: ANN001, ANN201
        claimed = await release.claim_release_token_managed_key()
        if claimed.status != "claimed" or not claimed.managed_secret_key:
            return _provider_claim_failure(claimed)
        credential_ref = claimed.managed_credential_ref
        if not credential_ref:
            return ManagedClaimResult("claimed", (), None, "credential_missing")
        return ManagedClaimResult(
            "claimed",
            (
                ManagedCredentialCandidate(
                    credential_ref=credential_ref,
                    source=_MANAGED_CLAIM_SOURCE_RELEASE_TOKEN,
                    secret_value=claimed.managed_secret_key,
                    delivery_ack=claimed.delivery_ack,
                    settings_values=_managed_settings_patch(
                        release.managed_state,
                        source=MANAGED_AUTH_CLAIM_SOURCE_DISCORD,
                        credential_ref=credential_ref,
                        expires_at=claimed.expires_at,
                        referral_id=claimed.referral_id,
                        delivery_ack=claimed.delivery_ack,
                        installation_id=None,
                    ),
                    ack_secret_key=(
                        secret_key_for_ack_source(claimed.delivery_ack.source)
                        if claimed.delivery_ack is not None
                        else None
                    ),
                    referral_bonus_applied=claimed.referral_bonus_applied,
                    auxiliary_secrets=_managed_user_secrets(
                        claimed.openrouter_user_id,
                        release.managed_state.installation_id,
                    ),
                    post_ack_settings_values=(
                        _managed_ack_clear_patch() if claimed.delivery_ack is not None else {}
                    ),
                    ack_delivered_settings_values=(
                        _managed_ack_delivered_patch() if claimed.delivery_ack is not None else {}
                    ),
                ),
            ),
            credential_ref,
        )

    async def _claim_discord(self, release, request):  # noqa: ANN001, ANN201
        claim_input = request.claim_input or ManagedClaimInput()

        def authorization_url(url: str) -> None:
            self.browser.set_authorization_url(url)
            callback = self.on_browser_available
            if callable(callback):
                callback(True)

        claimed = await release.claim_discord_managed_key(
            referral_id=claim_input.referral_id,
            on_authorization_url=authorization_url,
        )
        if claimed.status != "claimed" or not claimed.managed_secret_key:
            return _provider_claim_failure(claimed)
        credential_ref = claimed.managed_credential_ref
        if not credential_ref:
            return ManagedClaimResult("claimed", (), None, "credential_missing")
        identity = claimed.identity
        installation_id = None if identity is None else identity.bundle.installation_id
        return ManagedClaimResult(
            "claimed",
            (
                ManagedCredentialCandidate(
                    credential_ref=credential_ref,
                    source=MANAGED_AUTH_CLAIM_SOURCE_DISCORD,
                    secret_value=claimed.managed_secret_key,
                    delivery_ack=claimed.delivery_ack,
                    settings_values=_managed_settings_patch(
                        release.managed_state,
                        source=MANAGED_AUTH_CLAIM_SOURCE_DISCORD,
                        credential_ref=credential_ref,
                        expires_at=claimed.expires_at,
                        referral_id=claimed.referral_id,
                        delivery_ack=claimed.delivery_ack,
                        installation_id=installation_id,
                    ),
                    ack_secret_key=(
                        secret_key_for_ack_source(MANAGED_AUTH_CLAIM_SOURCE_DISCORD)
                        if claimed.delivery_ack is not None
                        else None
                    ),
                    referral_bonus_applied=claimed.referral_bonus_applied,
                    auxiliary_secrets=(
                        *((() if identity is None else identity.secret_values)),
                        *_managed_user_secrets(
                            claimed.openrouter_user_id,
                            installation_id,
                        ),
                    ),
                    clear_secret_keys=(() if identity is None else identity.clear_secret_keys),
                    post_ack_settings_values=(
                        _managed_ack_clear_patch() if claimed.delivery_ack is not None else {}
                    ),
                    ack_delivered_settings_values=(
                        _managed_ack_delivered_patch() if claimed.delivery_ack is not None else {}
                    ),
                ),
            ),
            credential_ref,
        )

    async def _claim_qq(self, release, request):  # noqa: ANN001, ANN201
        claim_input = request.claim_input
        if (
            claim_input is None
            or not claim_input.identity
            or not claim_input.credential
            or not claim_input.asserted_at
        ):
            return ManagedClaimResult("failed", detail_code="qq_claim_input_invalid")
        claimed = await release.claim_qq_managed_key(
            identity=claim_input.identity,
            credential=claim_input.credential,
            asserted_at=claim_input.asserted_at,
        )
        if claimed.status != "claimed" or not claimed.managed_secret_key:
            return ManagedClaimResult("failed", detail_code=claimed.detail_code)
        credential_ref = claimed.managed_credential_ref
        if not credential_ref:
            return ManagedClaimResult("claimed", (), None, "credential_missing")
        return ManagedClaimResult(
            "claimed",
            (
                ManagedCredentialCandidate(
                    credential_ref=credential_ref,
                    source=MANAGED_AUTH_CLAIM_SOURCE_QQ,
                    secret_value=claimed.managed_secret_key,
                    delivery_ack=claimed.delivery_ack,
                    settings_values=_managed_settings_patch(
                        release.managed_state,
                        source=MANAGED_AUTH_CLAIM_SOURCE_QQ,
                        credential_ref=credential_ref,
                        expires_at=claimed.expires_at,
                        referral_id=None,
                        delivery_ack=claimed.delivery_ack,
                        installation_id=None,
                    ),
                    ack_secret_key=(
                        secret_key_for_ack_source(MANAGED_AUTH_CLAIM_SOURCE_QQ)
                        if claimed.delivery_ack is not None
                        else None
                    ),
                    auxiliary_secrets=_managed_user_secrets(
                        claimed.openrouter_user_id,
                        release.managed_state.installation_id,
                    ),
                    post_ack_settings_values=(
                        _managed_ack_clear_patch() if claimed.delivery_ack is not None else {}
                    ),
                    ack_delivered_settings_values=(
                        _managed_ack_delivered_patch() if claimed.delivery_ack is not None else {}
                    ),
                ),
            ),
            credential_ref,
        )


@dataclass(slots=True)
class ProductionManagedDeliveryAckOwner:
    runtime_host: object
    secret_store: object

    async def acknowledge(self, request: ManagedAckRequest) -> ManagedAckResult:
        release = await self.runtime_host.resolve_managed_release_service()
        if release is None or getattr(release, "client", None) is None:
            return ManagedAckResult(False, "managed_release_unavailable")
        managed_state = release.managed_state
        if (
            managed_state.pending_delivery_ack_source != request.source
            or managed_state.pending_delivery_ack_delivery_id != request.delivery_id
            or managed_state.pending_delivery_ack_managed_credential_ref != request.credential_ref
        ):
            return ManagedAckResult(False, "ack_identity_mismatch")
        secret_store = self.secret_store() if callable(self.secret_store) else self.secret_store
        result = await ManagedKeyDeliveryAckService(
            broker_client=release.client,
            secret_store=secret_store,
            managed_state=managed_state,
        ).acknowledge_pending(clear_on_success=False)
        return ManagedAckResult(result.succeeded, result.status)


@dataclass(slots=True)
class ProductionManagedReleaseTransactionPort:
    coordinator: ManagedCanonicalTransactionCoordinator
    repository: ProductionCanonicalSettingsRepository
    persistence: object
    secret_commands: object

    async def ensure_key_for_llm_start(
        self,
        release,
        *,
        available_api_key: str | None,
    ) -> ManagedOpenRouterReleaseResult:  # noqa: ANN001
        receipt = await self.repository.load_receipt()
        managed = receipt.envelope.state.managed_connection
        if (
            managed.pending_delivery_ack_source
            and managed.pending_delivery_ack_delivery_id
            and managed.pending_delivery_ack_managed_credential_ref
        ):
            ack_secret_key = secret_key_for_ack_source(managed.pending_delivery_ack_source)
            delivery_confirmed = bool(managed.pending_delivery_ack_delivered)
            if not delivery_confirmed:
                delivery_confirmed = ack_delivery_confirmation_matches(
                    await self.secret_commands().resolve_secret_value(
                        ack_delivered_secret_key(ack_secret_key)
                    ),
                    source=managed.pending_delivery_ack_source,
                    delivery_id=managed.pending_delivery_ack_delivery_id,
                    credential_ref=managed.pending_delivery_ack_managed_credential_ref,
                )
            recovery = await self.coordinator.resume_pending_ack(
                ManagedPendingAckRecovery(
                    correlation_id=str(uuid4()),
                    source=managed.pending_delivery_ack_source,
                    delivery_id=managed.pending_delivery_ack_delivery_id,
                    credential_ref=managed.pending_delivery_ack_managed_credential_ref,
                    expires_at=managed.pending_delivery_ack_expires_at,
                    ack_secret_key=ack_secret_key,
                    post_ack_settings_values=_managed_ack_clear_patch(),
                    delivered=managed.pending_delivery_ack_delivered,
                    delivery_confirmed=delivery_confirmed,
                )
            )
            if recovery.state.stage in {
                ManagedTransactionStage.RETRY_ACK,
                ManagedTransactionStage.RETRY_CLEANUP,
            }:
                return _record_release_outcome(
                    release,
                    _managed_release_retry(recovery.state.detail_code),
                )
            if recovery.state.stage is not ManagedTransactionStage.COMPLETED:
                return _record_release_outcome(
                    release,
                    ManagedOpenRouterReleaseResult(
                        behavior=ManagedOpenRouterReleaseBehavior.STOP,
                        message_key="managed_release.stop",
                        diagnostics=ManagedOpenRouterReleaseDiagnostics(
                            operation="managed_key_delivery_ack",
                            code=recovery.state.detail_code or "managed_delivery_ack_failed",
                            error_class="terminal",
                        ),
                    ),
                )
            receipt = recovery.settings_receipt or await self.repository.load_receipt()
        api_key = available_api_key
        if api_key:
            return _record_release_outcome(
                release,
                ManagedOpenRouterReleaseResult(
                    behavior=ManagedOpenRouterReleaseBehavior.READY,
                    message_key="managed_release.ready",
                    api_key=api_key,
                    local_key_available=True,
                ),
            )
        if not getattr(release.managed_state, "release_token", None):
            return _record_release_outcome(
                release,
                ManagedOpenRouterReleaseResult(
                    behavior=ManagedOpenRouterReleaseBehavior.RESTART,
                    message_key="managed_release.restart",
                    diagnostics=ManagedOpenRouterReleaseDiagnostics(
                        operation="ensure_key_for_llm_start",
                        code="managed_authentication_requires_coordinator",
                        error_class="terminal",
                    ),
                ),
            )
        transaction_id = str(uuid4())
        result = await self.coordinator.execute(
            ManagedTransactionRequest(
                transaction_id=transaction_id,
                idempotency_key=transaction_id,
                correlation_id=transaction_id,
                claim_source=_MANAGED_CLAIM_SOURCE_RELEASE_TOKEN,
                local_secret_key=OPENROUTER_MANAGED_API_KEY_SECRET,
                settings_values=self.persistence.values_for(receipt.envelope),
                expected_settings_revision=receipt.revision,
                reason="managed_release_token_claim",
            )
        )
        if result.state.stage is ManagedTransactionStage.COMPLETED:
            api_key = await self.secret_commands().resolve_secret_value(
                OPENROUTER_MANAGED_API_KEY_SECRET
            )
            if api_key:
                return _record_release_outcome(
                    release,
                    ManagedOpenRouterReleaseResult(
                        behavior=ManagedOpenRouterReleaseBehavior.READY,
                        message_key="managed_release.ready",
                        api_key=api_key,
                        local_key_available=True,
                    ),
                )
        if result.state.stage is ManagedTransactionStage.TERMINAL_FAILURE and isinstance(
            result.release_outcome, ManagedOpenRouterReleaseResult
        ):
            return _record_release_outcome(release, result.release_outcome)
        return _record_release_outcome(
            release,
            _managed_release_retry(result.state.detail_code),
        )


def create_production_managed_authentication_application(
    *,
    runtime_host,
    ui_settings,
    secrets,
    oauth_runtime,
    canonical_commands,
    persistence,
    state_path,
) -> ManagedAuthenticationApplication:  # noqa: ANN001
    settings_repository = ProductionCanonicalSettingsRepository(persistence, state_path)
    browser = ProductionManagedAuthenticationBrowser(oauth_runtime)
    browser_state: dict[str, object] = {
        "authorization_url": None,
        "callback_received": False,
        "referral_bonus_applied": False,
    }
    application: ManagedAuthenticationApplication | None = None
    coordinator: ManagedCanonicalTransactionCoordinator | None = None
    pending_retries: dict[str, str] = {}

    def callback_received(_event: object) -> None:
        browser_state["callback_received"] = True
        if application is not None:
            application.set_callback_received()

    runtime_host.subscribe_managed_discord_callback(callback_received)

    async def presentation() -> ManagedAuthenticationPresentation:
        snapshot = await ui_settings.snapshot()
        connection = snapshot.translation.connection or ""
        selected = connection in {"managed", "managed_china"}
        managed_key = next(
            (
                entry
                for entry in snapshot.credentials.entries
                if entry.key == "openrouter_managed_api_key"
            ),
            None,
        )
        available = bool(managed_key is not None and managed_key.present)
        return managed_authentication_presentation(
            action="continue" if not selected or available else "prompt",
            prompt="qq" if connection == "managed_china" else "discord",
            connection_state="connected" if available else "disconnected",
            browser_reopen_available=browser.available,
            referral_bonus_applied=bool(browser_state["referral_bonus_applied"]),
            trial_remaining_percent=snapshot.managed.trial_remaining_percent,
            referral_id=snapshot.managed.referral_id,
            pass_status=snapshot.managed.pass_status,
            callback_received=bool(browser_state["callback_received"]),
        )

    async def start_discord(referral_id: str | None) -> tuple[str, str | None]:
        browser_state["callback_received"] = False
        assert coordinator is not None
        result = await _execute_or_retry_managed_transaction(
            coordinator=coordinator,
            pending_retries=pending_retries,
            repository=settings_repository,
            persistence=persistence,
            source=MANAGED_AUTH_CLAIM_SOURCE_DISCORD,
            local_secret_key=OPENROUTER_MANAGED_API_KEY_SECRET,
            claim_input=ManagedClaimInput(referral_id=referral_id),
        )
        browser_state["referral_bonus_applied"] = result.state.referral_bonus_applied
        return _authentication_outcome(result, MANAGED_AUTH_CLAIM_SOURCE_DISCORD)

    async def start_qq(identity: str, credential: str) -> tuple[str, str | None]:
        assert coordinator is not None
        result = await _execute_or_retry_managed_transaction(
            coordinator=coordinator,
            pending_retries=pending_retries,
            repository=settings_repository,
            persistence=persistence,
            source=MANAGED_AUTH_CLAIM_SOURCE_QQ,
            local_secret_key=OPENROUTER_MANAGED_QQ_API_KEY_SECRET,
            claim_input=ManagedClaimInput(
                identity=identity,
                credential=credential,
                asserted_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        return _authentication_outcome(result, MANAGED_AUTH_CLAIM_SOURCE_QQ)

    async def cancel() -> None:
        browser_state["authorization_url"] = None
        if application is not None:
            application.set_browser_reopen_available(False)

    async def recover_pending() -> bool:
        assert coordinator is not None
        receipt = await settings_repository.load_receipt()
        managed = receipt.envelope.state.managed_connection
        if not (
            managed.pending_delivery_ack_source
            and managed.pending_delivery_ack_delivery_id
            and managed.pending_delivery_ack_managed_credential_ref
        ):
            return True
        ack_secret_key = secret_key_for_ack_source(managed.pending_delivery_ack_source)
        delivery_confirmed = bool(managed.pending_delivery_ack_delivered)
        if not delivery_confirmed:
            delivery_confirmed = ack_delivery_confirmation_matches(
                await canonical_commands.secret_commands.resolve_secret_value(
                    ack_delivered_secret_key(ack_secret_key)
                ),
                source=managed.pending_delivery_ack_source,
                delivery_id=managed.pending_delivery_ack_delivery_id,
                credential_ref=managed.pending_delivery_ack_managed_credential_ref,
            )
        result = await coordinator.resume_pending_ack(
            ManagedPendingAckRecovery(
                correlation_id=str(uuid4()),
                source=managed.pending_delivery_ack_source,
                delivery_id=managed.pending_delivery_ack_delivery_id,
                credential_ref=managed.pending_delivery_ack_managed_credential_ref,
                expires_at=managed.pending_delivery_ack_expires_at,
                ack_secret_key=ack_secret_key,
                post_ack_settings_values=_managed_ack_clear_patch(),
                delivered=managed.pending_delivery_ack_delivered,
                delivery_confirmed=delivery_confirmed,
            )
        )
        return result.state.stage not in {
            ManagedTransactionStage.RETRY_ACK,
            ManagedTransactionStage.RETRY_CLEANUP,
        }

    application = ManagedAuthenticationApplication(
        presentation=presentation,
        start_discord=start_discord,
        start_qq=start_qq,
        browser=browser,
        close_authentication=cancel,
        oauth_runtime=oauth_runtime,
        recover_pending=recover_pending,
    )
    claim_owner = ProductionManagedClaimOwner(
        runtime_host=runtime_host,
        secret_store=lambda: canonical_commands._secret_port,
        browser=browser,
        on_browser_available=(
            lambda available: application.set_browser_reopen_available(available)
        ),
    )
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=application,
        claim=claim_owner,
        secrets=lambda: canonical_commands.secret_commands,
        secret_store=lambda: canonical_commands._secret_port,
        settings=settings_repository,
        runtime=canonical_commands.runtime_apply,
        delivery_ack=ProductionManagedDeliveryAckOwner(
            runtime_host, lambda: canonical_commands._secret_port
        ),
    )
    release_transaction_port = ProductionManagedReleaseTransactionPort(
        coordinator=coordinator,
        repository=settings_repository,
        persistence=persistence,
        secret_commands=lambda: canonical_commands.secret_commands,
    )
    bind_release_transaction = getattr(runtime_host, "bind_managed_transaction_port", None)
    if callable(bind_release_transaction):
        bind_release_transaction(release_transaction_port)
    application.managed_transactions = coordinator
    return application


async def _execute_managed_transaction(
    *,
    coordinator: ManagedCanonicalTransactionCoordinator,
    repository: ProductionCanonicalSettingsRepository,
    persistence,
    source: str,
    local_secret_key: str,
    claim_input: ManagedClaimInput,
):  # noqa: ANN001, ANN201
    receipt = await repository.load_receipt()
    transaction_id = str(uuid4())
    return await coordinator.execute(
        ManagedTransactionRequest(
            transaction_id=transaction_id,
            idempotency_key=transaction_id,
            correlation_id=transaction_id,
            claim_source=source,
            local_secret_key=local_secret_key,
            settings_values=persistence.values_for(receipt.envelope),
            expected_settings_revision=receipt.revision,
            reason=f"managed_{source}_claim",
            claim_input=claim_input,
        )
    )


async def _execute_or_retry_managed_transaction(
    *,
    coordinator: ManagedCanonicalTransactionCoordinator,
    pending_retries: dict[str, str],
    repository: ProductionCanonicalSettingsRepository,
    persistence,
    source: str,
    local_secret_key: str,
    claim_input: ManagedClaimInput,
):  # noqa: ANN001, ANN201
    pending = pending_retries.get(source)
    if pending is not None:
        result = await coordinator.retry_idempotency(pending)
    else:
        result = await _execute_managed_transaction(
            coordinator=coordinator,
            repository=repository,
            persistence=persistence,
            source=source,
            local_secret_key=local_secret_key,
            claim_input=claim_input,
        )
    if result.state.stage in {
        ManagedTransactionStage.RETRY_ACK,
        ManagedTransactionStage.RETRY_CLEANUP,
    }:
        pending_retries[source] = result.state.idempotency_key
    else:
        pending_retries.pop(source, None)
    return result


def _authentication_outcome(result, source: str) -> tuple[str, str | None]:  # noqa: ANN001
    if result.state.stage is ManagedTransactionStage.COMPLETED:
        return "applied", None
    if result.state.detail_code == "managed_release_unavailable":
        return (
            "unavailable",
            (
                "qq_managed_auth.broker_unavailable"
                if source == MANAGED_AUTH_CLAIM_SOURCE_QQ
                else "discord_auth.error.retry"
            ),
        )
    detail = result.state.detail_code
    if detail and detail.startswith(("qq_managed_auth.", "discord_auth.")):
        return "rejected", detail
    if source == MANAGED_AUTH_CLAIM_SOURCE_QQ:
        return "rejected", "qq_managed_auth.key_unavailable"
    return "rejected", "discord_auth.error.retry"


def _managed_settings_patch(
    managed_state,
    *,
    source: str,
    credential_ref: str,
    expires_at: str | None,
    referral_id: str | None,
    delivery_ack,
    installation_id: str | None,
) -> dict[str, object]:  # noqa: ANN001
    sources = normalize_managed_claim_sources((*managed_state.local_managed_claim_sources, source))
    values: dict[str, object] = {
        "active_managed_credential_ref": credential_ref,
        "active_managed_expires_at": expires_at,
        "local_managed_claim_sources": list(sources),
        "release_token": None,
        "release_token_expires_at": None,
        "verified_hardware_hash": None,
        "verified_hardware_hash_salt_version": None,
    }
    if referral_id is not None:
        values["referral_id"] = referral_id
    if installation_id is not None:
        values["installation_id"] = installation_id
    if delivery_ack is not None:
        values.update(
            {
                "pending_delivery_ack_source": delivery_ack.source,
                "pending_delivery_ack_delivery_id": delivery_ack.delivery_id,
                "pending_delivery_ack_managed_credential_ref": (
                    delivery_ack.managed_credential_ref
                ),
                "pending_delivery_ack_expires_at": delivery_ack.expires_at,
                "pending_delivery_ack_delivered": False,
            }
        )
    return {"state": {"managed_connection": values}}


def _message_detail(message, fallback: str) -> str:  # noqa: ANN001
    return fallback if message is None else message.key


def _transaction_detail(result, fallback: str) -> str:  # noqa: ANN001
    return _message_detail(result.message, fallback)


def _managed_user_secrets(
    user_id: object,
    installation_id: object,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(user_id, str) or not user_id.strip():
        return ()
    if len(user_id.strip()) > OPENROUTER_MANAGED_USER_ID_MAX_LENGTH:
        return ()
    if not isinstance(installation_id, str) or not installation_id.strip():
        return ()
    return (
        (OPENROUTER_MANAGED_USER_ID_SECRET, user_id.strip()),
        (OPENROUTER_MANAGED_USER_INSTALLATION_ID_SECRET, installation_id.strip()),
    )


def _managed_ack_clear_patch() -> dict[str, object]:
    return {
        "state": {
            "managed_connection": {
                "pending_delivery_ack_source": None,
                "pending_delivery_ack_delivery_id": None,
                "pending_delivery_ack_managed_credential_ref": None,
                "pending_delivery_ack_expires_at": None,
                "pending_delivery_ack_delivered": False,
            }
        }
    }


def _managed_ack_delivered_patch() -> dict[str, object]:
    return {
        "state": {
            "managed_connection": {
                "pending_delivery_ack_delivered": True,
            }
        }
    }


def _managed_release_state_clear_patch() -> dict[str, object]:
    return {
        "state": {
            "managed_connection": {
                "release_token": None,
                "release_token_expires_at": None,
                "verified_hardware_hash": None,
                "verified_hardware_hash_salt_version": None,
            }
        }
    }


def _provider_claim_failure(claimed) -> ManagedClaimResult:  # noqa: ANN001
    values = _managed_release_state_clear_patch() if claimed.clear_temporary_state else {}
    identity = claimed.identity
    if identity is not None:
        managed = values.setdefault("state", {}).setdefault("managed_connection", {})
        managed["installation_id"] = identity.bundle.installation_id
    return ManagedClaimResult(
        "failed",
        detail_code=claimed.detail_code,
        failure_settings_values=values,
        release_outcome=claimed.release_result,
        failure_auxiliary_secrets=(() if identity is None else identity.secret_values),
        failure_clear_secret_keys=(() if identity is None else identity.clear_secret_keys),
    )


def _managed_release_retry(detail_code: str | None) -> ManagedOpenRouterReleaseResult:
    return ManagedOpenRouterReleaseResult(
        behavior=ManagedOpenRouterReleaseBehavior.RETRY,
        message_key="managed_release.retry",
        diagnostics=ManagedOpenRouterReleaseDiagnostics(
            operation="ensure_key_for_llm_start",
            code=detail_code or "managed_transaction_retry",
            error_class="retryable",
        ),
    )


def _record_release_outcome(
    release,
    result: ManagedOpenRouterReleaseResult,
) -> ManagedOpenRouterReleaseResult:  # noqa: ANN001
    record = getattr(release, "record_transaction_outcome", None)
    if callable(record):
        record(result)
    return result


__all__ = [
    "ProductionManagedDeliveryAckOwner",
    "ProductionManagedReleaseTransactionPort",
    "ProductionManagedAuthenticationBrowser",
    "ProductionManagedClaimOwner",
    "create_production_managed_authentication_application",
]
