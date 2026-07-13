from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace

import pytest

from puripuly_heart.app.adapters.managed_authentication_production import (
    ProductionManagedAuthenticationBrowser,
    ProductionManagedClaimOwner,
    ProductionManagedDeliveryAckOwner,
    ProductionManagedReleaseTransactionPort,
)
from puripuly_heart.app.adapters.openrouter_pkce_production import (
    ProductionCanonicalSettingsRepository,
)
from puripuly_heart.app.adapters.settings_vnext_canonical_persistence import (
    SettingsVNextCanonicalPersistenceAdapter,
)
from puripuly_heart.app.ports.broker_client import (
    ManagedKeyDeliveryAckMetadata,
    ManagedKeyDeliveryAckResult,
    QqManagedAssertionResult,
    QqManagedEntitlementSnapshot,
)
from puripuly_heart.app.ports.secret_store import (
    SecretReadResult,
    SecretSnapshot,
    SecretWriteResult,
)
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitReceipt,
    SettingsCommitRequest,
    SettingsCommitResult,
    SettingsSnapshot,
)
from puripuly_heart.app.services.canonical_command_composition import (
    CanonicalCommandComposition,
)
from puripuly_heart.app.services.canonical_secret_commands import (
    CanonicalSecretCommandService,
    SyncSecretStorePortAdapter,
)
from puripuly_heart.app.services.managed_canonical_transaction import (
    ManagedAckResult,
    ManagedCanonicalTransactionCoordinator,
    ManagedClaimInput,
    ManagedClaimResult,
    ManagedCredentialCandidate,
    ManagedPendingAckRecovery,
    ManagedTransactionRequest,
    ManagedTransactionStage,
    encode_ack_delivery_confirmation,
)
from puripuly_heart.config.settings_vnext import serialization
from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.managed_identity import (
    prepare_managed_identity_bundle,
    prepare_replacement_managed_identity_bundle,
)
from puripuly_heart.core.managed_openrouter_release import (
    ManagedOpenRouterReleaseBehavior,
    ManagedOpenRouterReleaseDiagnostics,
    ManagedOpenRouterReleaseResult,
    ManagedProviderClaimResult,
)
from puripuly_heart.core.messages import ErrorDiagnostics, RuntimeApplyResult
from puripuly_heart.core.runtime.oauth import OAuthRuntime
from puripuly_heart.core.storage.secrets import InMemorySecretStore


class Store:
    def __init__(self) -> None:
        self.values = {"managed": "old"}
        self.fail: str | None = None
        self.events: list[str] = []
        self.compare_hook = None

    async def get_secret(self, key):  # noqa: ANN001, ANN201
        self.events.append("get")
        value = self.values.get(key)
        return SecretReadResult(key, value, _test_secret_revision(value), None, None)

    async def snapshot_secret(self, key):  # noqa: ANN001, ANN201
        self.events.append("snapshot")
        if self.fail == "snapshot":
            raise RuntimeError
        value = self.values.get(key)
        return SecretSnapshot(key, value, _test_secret_revision(value), value is not None)

    async def set_secret(self, key, value):  # noqa: ANN001, ANN201
        self.events.append("secret")
        if self.fail == "secret":
            raise RuntimeError
        self.values[key] = value
        return SecretWriteResult(True, key, "secret-r2", None, None)

    async def clear_secret(self, key):  # noqa: ANN001, ANN201
        self.values.pop(key, None)
        return SecretWriteResult(True, key, None, None, None)

    async def restore_secret(self, snapshot):  # noqa: ANN001, ANN201
        self.events.append("restore_secret")
        if self.fail == "restore_secret":
            raise RuntimeError
        if snapshot.existed:
            self.values[snapshot.key] = snapshot.value
        else:
            self.values.pop(snapshot.key, None)
        return SecretWriteResult(True, snapshot.key, snapshot.revision, None, None)

    async def compare_and_clear_secret(self, key, expected_revision):  # noqa: ANN001, ANN201
        from puripuly_heart.app.ports.secret_store import SecretCompareAndClearResult

        hook = self.compare_hook
        self.compare_hook = None
        if callable(hook):
            hook(key)
        value = self.values.get(key)
        if value is None:
            status = "absent"
        elif _test_secret_revision(value) != expected_revision:
            status = "stale"
        else:
            self.values.pop(key, None)
            status = "cleared"
        return SecretCompareAndClearResult(status, key, expected_revision)


class Repository:
    def __init__(self) -> None:
        self.receipt = SettingsCommitReceipt(AppSettingsVNext(), "r1", "before", "before")
        self.fail: str | None = None
        self.events: list[str] = []
        self.saves = 0
        self.requests = []

    async def load_receipt(self):  # noqa: ANN201
        self.events.append("load")
        if self.fail == "load":
            raise RuntimeError
        return self.receipt

    async def save(self, request):  # noqa: ANN001, ANN201
        self.events.append("commit")
        self.saves += 1
        self.requests.append(request)
        if self.fail == "commit" and self.saves == 1:
            raise RuntimeError
        if self.fail == "rollback" and self.saves == 2:
            raise RuntimeError
        if self.fail == "cleanup" and self.saves == 3:
            raise RuntimeError
        if self.fail == "conflict" and self.saves == 1:
            diagnostics = ErrorDiagnostics(
                component="settings",
                operation="commit",
                code="revision_conflict",
                category="transaction",
                visibility="basic",
                content_policy="metadata_only",
                status_code=None,
                retry_after_ms=None,
                fields={},
            )
            return SettingsCommitResult(False, None, None, diagnostics)
        revision = f"r{self.saves + 1}"
        values = serialization.to_dict(self.receipt.envelope)
        _merge_test_values(values, request.values)
        self.receipt = SettingsCommitReceipt(
            serialization.from_dict(values),
            revision,
            request.reason,
            request.correlation_id,
        )
        return SettingsCommitResult(
            True,
            SettingsSnapshot(request.values, revision),
            None,
            None,
            self.receipt,
        )


class Runtime:
    def __init__(self) -> None:
        self.fail = False
        self.fail_all = False
        self.events: list[str] = []
        self.calls = 0

    async def apply_runtime(self, _request):  # noqa: ANN001, ANN201
        self.events.append("apply")
        self.calls += 1
        return RuntimeApplyResult(
            "failed" if self.fail_all or (self.fail and self.calls == 1) else "applied",
            None,
            None,
        )


def _merge_test_values(target, patch) -> None:  # noqa: ANN001
    for key, value in patch.items():
        if isinstance(target.get(key), dict) and isinstance(value, dict | Mapping):
            _merge_test_values(target[key], value)
        else:
            target[key] = _test_mutable(value)


def _test_mutable(value):  # noqa: ANN001, ANN201
    if isinstance(value, Mapping):
        return {key: _test_mutable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_test_mutable(item) for item in value]
    return value


def _test_secret_revision(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def _replace_encrypted_secret(path: str, passphrase: str, key: str, value: str) -> None:
    from pathlib import Path

    from puripuly_heart.core.storage.secrets import EncryptedFileSecretStore

    EncryptedFileSecretStore(Path(path), passphrase=passphrase).set(key, value)


def _set_pending_ack_receipt(repository: Repository, *, delivered: bool = False) -> None:
    settings = repository.receipt.envelope
    repository.receipt = SettingsCommitReceipt(
        replace(
            settings,
            state=replace(
                settings.state,
                managed_connection=replace(
                    settings.state.managed_connection,
                    pending_delivery_ack_source="discord",
                    pending_delivery_ack_delivery_id="delivery",
                    pending_delivery_ack_managed_credential_ref="credential",
                    pending_delivery_ack_delivered=delivered,
                ),
            ),
        ),
        repository.receipt.revision,
        repository.receipt.reason,
        repository.receipt.correlation_id,
    )


def _revision_conflict_result() -> SettingsCommitResult:
    return SettingsCommitResult(
        False,
        None,
        None,
        ErrorDiagnostics(
            component="settings",
            operation="save",
            code="revision_conflict",
            category="transaction",
            visibility="basic",
            content_policy="metadata_only",
            status_code=None,
            retry_after_ms=None,
            fields={},
        ),
    )


class Claim:
    def __init__(self, result: ManagedClaimResult) -> None:
        self.result = result
        self.calls = 0
        self.events: list[str] = []

    async def claim(self, _request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.events.append("claim")
        return self.result


class Ack:
    def __init__(self) -> None:
        self.results = [ManagedAckResult(True, "acknowledged")]
        self.calls = 0
        self.requests = []

    async def acknowledge(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.requests.append(request)
        return self.results.pop(0)


@dataclass
class Harness:
    coordinator: ManagedCanonicalTransactionCoordinator
    claim: Claim
    store: Store
    repository: Repository
    runtime: Runtime
    ack: Ack


def request(**changes) -> ManagedTransactionRequest:  # noqa: ANN003
    values = {
        "transaction_id": "transaction-1",
        "idempotency_key": "idempotency-1",
        "correlation_id": "correlation-1",
        "claim_source": "discord",
        "local_secret_key": "managed",
        "settings_values": {"intent": {"translation": {"connection": "managed"}}},
        "expected_settings_revision": "r1",
        "reason": "managed_claim",
    }
    values.update(changes)
    return ManagedTransactionRequest(**values)


def harness(
    *,
    claim_status: str = "claimed",
    candidates: tuple[ManagedCredentialCandidate, ...] | None = None,
    external_claim_ref: str | None = "external-1",
) -> Harness:
    ack_metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery-1",
        managed_credential_ref="credential-1",
        expires_at=None,
        delivery_ack_token="ack-secret",
    )
    selected = candidates or (
        ManagedCredentialCandidate("credential-1", "discord", "new-secret", ack_metadata),
    )
    claim = Claim(ManagedClaimResult(claim_status, selected, external_claim_ref))
    store = Store()
    repository = Repository()
    runtime = Runtime()
    ack = Ack()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=ack,
    )
    return Harness(coordinator, claim, store, repository, runtime, ack)


@pytest.mark.asyncio
async def test_success_is_exactly_claim_secret_commit_apply_ack_and_secret_free() -> None:
    owned = harness()

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert result.state.credential_ref == "credential-1"
    assert result.secret_metadata == ("managed", "secret-r2")
    assert result.secret_receipts == (("managed", "secret-r2"),)
    assert result.settings_receipt is not None
    assert result.settings_receipt.revision == "r2"
    assert result.runtime_status == "applied"
    assert owned.claim.calls == owned.ack.calls == 1
    assert owned.claim.events + owned.store.events[
        :2
    ] + owned.repository.events + owned.runtime.events == [
        "claim",
        "snapshot",
        "secret",
        "load",
        "commit",
        "apply",
    ]
    assert "new-secret" not in repr(result)
    assert "ack-secret" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "stage", "detail"),
    [
        ("claim", ManagedTransactionStage.TERMINAL_FAILURE, "claim_failed"),
        ("conflict", ManagedTransactionStage.CONFLICT, "claim_conflict"),
        ("secret", ManagedTransactionStage.COMPENSATION_REQUIRED, "secret_failed"),
        ("commit", ManagedTransactionStage.COMPENSATION_REQUIRED, "commit_failed"),
        ("revision", ManagedTransactionStage.CONFLICT, "revision_conflict"),
        ("apply", ManagedTransactionStage.COMPENSATION_REQUIRED, "runtime_apply_failed"),
    ],
)
async def test_forced_failure_matrix(failure, stage, detail) -> None:  # noqa: ANN001
    if failure == "claim":
        owned = harness(claim_status="failed", external_claim_ref=None)
    elif failure == "conflict":
        owned = harness(claim_status="conflict", external_claim_ref=None)
        owned.claim.result = ManagedClaimResult("conflict", (), None, "claim_conflict")
    else:
        owned = harness()
        if failure == "secret":
            owned.store.fail = "secret"
        elif failure == "commit":
            owned.repository.fail = "commit"
        elif failure == "revision":
            owned = harness(external_claim_ref=None)
            owned.repository.fail = "conflict"
        else:
            owned.runtime.fail = True

    result = await owned.coordinator.execute(request())

    assert result.state.stage is stage
    assert result.state.detail_code == detail
    if failure == "revision":
        assert result.diagnostics is not None
        assert result.diagnostics.code == "revision_conflict"
    if failure in {"secret", "commit", "revision", "apply"}:
        assert owned.store.values["managed"] == "old"


@pytest.mark.asyncio
async def test_ack_failure_has_one_idempotent_retry_without_reclaim_or_reselection() -> None:
    owned = harness()
    owned.ack.results = [
        ManagedAckResult(False, "temporary"),
        ManagedAckResult(True, "acknowledged"),
    ]

    pending = await owned.coordinator.execute(request())
    completed = await owned.coordinator.retry_idempotency("idempotency-1")
    duplicate = await owned.coordinator.execute(request())

    assert pending.state.stage is ManagedTransactionStage.RETRY_ACK
    assert pending.state.retry_supported is True
    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert duplicate == completed
    assert owned.claim.calls == 1
    assert owned.ack.calls == 2
    assert owned.repository.saves == 1
    assert {item.credential_ref for item in owned.ack.requests} == {"credential-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["ack_identity_mismatch", "missing_token", "permanent_rejection"],
)
async def test_permanent_ack_failures_are_terminal(status: str) -> None:
    owned = harness()
    owned.ack.results = [ManagedAckResult(False, status)]

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.TERMINAL_FAILURE
    assert result.state.detail_code == status
    assert result.state.retry_supported is False
    assert owned.ack.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["retryable", "managed_release_unavailable", "token_read_failed"],
)
async def test_actual_transient_ack_statuses_are_retryable(status: str) -> None:
    owned = harness()
    owned.ack.results = [
        ManagedAckResult(False, status),
        ManagedAckResult(True, "acknowledged"),
    ]

    pending = await owned.coordinator.execute(request())
    completed = await owned.coordinator.retry_idempotency("idempotency-1")

    assert pending.state.stage is ManagedTransactionStage.RETRY_ACK
    assert pending.state.retry_supported is True
    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.claim.calls == 1
    assert owned.ack.calls == 2


@pytest.mark.asyncio
async def test_cancelled_transient_ack_remains_retryable() -> None:
    owned = harness()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed(_request):  # noqa: ANN001, ANN202
        entered.set()
        await release.wait()
        owned.ack.calls += 1
        return ManagedAckResult(False, "retryable")

    owned.ack.acknowledge = delayed
    task = asyncio.create_task(owned.coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    owned.ack.acknowledge = Ack().acknowledge
    completed = await owned.coordinator.retry_idempotency("idempotency-1")
    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.claim.calls == 1


@pytest.mark.asyncio
async def test_expired_ack_is_terminal_without_remote_attempt() -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery",
        managed_credential_ref="credential",
        expires_at="2000-01-01T00:00:00Z",
        delivery_ack_token="ack-token",
    )
    owned = harness(
        candidates=(
            ManagedCredentialCandidate("credential", "discord", "managed-secret", metadata),
        )
    )

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.TERMINAL_FAILURE
    assert result.state.detail_code == "ack_expired"
    assert owned.ack.calls == 0


@pytest.mark.asyncio
async def test_ack_success_clears_token_and_returns_exact_final_cleanup_receipt() -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery",
        managed_credential_ref="credential",
        expires_at=None,
        delivery_ack_token="ack-token",
    )
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    owned = harness(candidates=(candidate,))

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert result.settings_receipt is not None
    assert result.settings_receipt.revision == "r4"
    assert tuple(receipt.revision for receipt in result.attempt_receipts) == ("r2", "r3", "r4")
    assert result.state.settings_revision == "r4"
    assert result.secret_receipts == (
        ("managed", "secret-r2"),
        ("ack-secret-key.delivered", None),
        ("ack-secret-key", "secret-r2"),
        ("ack-secret-key.delivered", "secret-r2"),
        ("ack-secret-key", None),
        ("ack-secret-key.delivered", None),
    )
    assert "ack-secret-key" not in owned.store.values
    assert owned.repository.saves == 3
    assert owned.runtime.calls == 3
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_post_ack_cleanup_failure_resumes_without_ack_replay() -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery",
        managed_credential_ref="credential",
        expires_at=None,
        delivery_ack_token="ack-token",
    )
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    owned = harness(candidates=(candidate,))
    owned.repository.fail = "cleanup"

    result = await owned.coordinator.execute(request())
    resumed = await owned.coordinator.retry_idempotency("idempotency-1")

    assert result.state.stage is ManagedTransactionStage.RETRY_CLEANUP
    assert result.state.detail_code == "ack_cleanup_failed"
    assert resumed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_persisted_ack_recovery_uses_coordinator_without_reclaim() -> None:
    owned = harness()
    owned.store.values["ack-secret-key"] = "ack-token"
    _set_pending_ack_receipt(owned.repository)

    result = await owned.coordinator.resume_pending_ack(
        ManagedPendingAckRecovery(
            correlation_id="recovery",
            source="discord",
            delivery_id="delivery",
            credential_ref="credential",
            expires_at=None,
            ack_secret_key="ack-secret-key",
            post_ack_settings_values={
                "state": {"managed_connection": {"pending_delivery_ack_source": None}}
            },
        )
    )

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert result.settings_receipt is not None
    assert result.settings_receipt.revision == "r3"
    assert owned.claim.calls == 0
    assert owned.ack.calls == 1
    assert "ack-secret-key" not in owned.store.values


@pytest.mark.asyncio
async def test_persisted_ack_recovery_cleanup_settles_after_cancellation() -> None:
    owned = harness()
    owned.store.values["ack-secret-key"] = "ack-token"
    _set_pending_ack_receipt(owned.repository)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = owned.repository.save

    async def blocked(request):  # noqa: ANN001, ANN202
        entered.set()
        await release.wait()
        return await original(request)

    owned.repository.save = blocked
    recovery = ManagedPendingAckRecovery(
        correlation_id="recovery",
        source="discord",
        delivery_id="delivery",
        credential_ref="credential",
        expires_at=None,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    task = asyncio.create_task(owned.coordinator.resume_pending_ack(recovery))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await owned.coordinator.resume_pending_ack(recovery)
    assert settled.state.stage is ManagedTransactionStage.COMPLETED
    assert settled.settings_receipt is not None
    assert settled.settings_receipt.revision == "r3"
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_process_loss_after_delivered_commit_resumes_cleanup_without_token_or_ack() -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery",
        managed_credential_ref="credential",
        expires_at=None,
        delivery_ack_token="ack-token",
    )
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        ack_delivered_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_delivered": True}}
        },
        post_ack_settings_values={
            "state": {
                "managed_connection": {
                    "pending_delivery_ack_source": None,
                    "pending_delivery_ack_delivered": False,
                }
            }
        },
    )
    owned = harness(candidates=(candidate,))
    owned.repository.fail = "cleanup"

    interrupted = await owned.coordinator.execute(request())

    assert interrupted.state.stage is ManagedTransactionStage.RETRY_CLEANUP
    assert interrupted.settings_receipt is not None
    assert interrupted.settings_receipt.revision == "r3"
    assert "ack-secret-key" not in owned.store.values
    assert owned.ack.calls == 1

    recovered = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=owned.claim,
        secrets=CanonicalSecretCommandService(owned.store),
        secret_store=owned.store,
        settings=owned.repository,
        runtime=owned.runtime,
        delivery_ack=owned.ack,
    )
    owned.repository.fail = None
    completed = await recovered.resume_pending_ack(
        ManagedPendingAckRecovery(
            correlation_id="recovery",
            source="discord",
            delivery_id="delivery",
            credential_ref="credential",
            expires_at=None,
            ack_secret_key="ack-secret-key",
            post_ack_settings_values=candidate.post_ack_settings_values,
            delivered=True,
        )
    )

    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_repeated_delivered_marker_failures_never_delete_token_or_replay_ack() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {
                "managed_connection": {
                    "pending_delivery_ack_source": None,
                    "pending_delivery_ack_delivery_id": None,
                    "pending_delivery_ack_managed_credential_ref": None,
                    "pending_delivery_ack_delivered": False,
                }
            }
        },
    )
    owned = harness(candidates=(candidate,))
    original = owned.repository.save
    failures = {"remaining": 2}

    async def fail_marker(request):  # noqa: ANN001, ANN202
        if request.reason == "managed_delivery_ack_delivered" and failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError
        return await original(request)

    owned.repository.save = fail_marker

    first = await owned.coordinator.execute(request())
    second = await owned.coordinator.retry_idempotency("idempotency-1")

    assert first.state.stage is ManagedTransactionStage.RETRY_CLEANUP
    assert second.state.stage is ManagedTransactionStage.RETRY_CLEANUP
    assert owned.ack.calls == 1
    assert owned.store.values["ack-secret-key"] == "ack-token"
    assert owned.store.values["ack-secret-key.delivered"] == encode_ack_delivery_confirmation(
        "discord", "delivery", "credential"
    )

    completed = await owned.coordinator.retry_idempotency("idempotency-1")

    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.ack.calls == 1
    assert "ack-secret-key" not in owned.store.values
    assert "ack-secret-key.delivered" not in owned.store.values


@pytest.mark.asyncio
async def test_new_same_source_delivery_rotates_stale_confirmation_before_token_store() -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        "discord", "new-delivery", "new-credential", None, "new-token"
    )
    candidate = ManagedCredentialCandidate(
        "new-credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    owned = harness(candidates=(candidate,))
    owned.store.values["ack-secret-key"] = "old-token"
    owned.store.values["ack-secret-key.delivered"] = encode_ack_delivery_confirmation(
        "discord", "old-delivery", "old-credential"
    )
    original_ack = owned.ack.acknowledge

    async def observe_rotation(request):  # noqa: ANN001, ANN202
        assert request.delivery_id == "new-delivery"
        assert owned.store.values["ack-secret-key"] == "new-token"
        assert "ack-secret-key.delivered" not in owned.store.values
        return await original_ack(request)

    owned.ack.acknowledge = observe_rotation

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_process_restart_uses_secret_confirmation_after_marker_commit_failure() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {
                "managed_connection": {
                    "pending_delivery_ack_source": None,
                    "pending_delivery_ack_delivery_id": None,
                    "pending_delivery_ack_managed_credential_ref": None,
                    "pending_delivery_ack_delivered": False,
                }
            }
        },
    )
    owned = harness(candidates=(candidate,))
    original = owned.repository.save
    failed = False

    async def fail_once(request):  # noqa: ANN001, ANN202
        nonlocal failed
        if request.reason == "managed_delivery_ack_delivered" and not failed:
            failed = True
            raise RuntimeError
        return await original(request)

    owned.repository.save = fail_once
    interrupted = await owned.coordinator.execute(request())
    assert interrupted.state.stage is ManagedTransactionStage.RETRY_CLEANUP
    assert owned.ack.calls == 1
    assert owned.store.values["ack-secret-key"] == "ack-token"

    recovered = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=owned.claim,
        secrets=CanonicalSecretCommandService(owned.store),
        secret_store=owned.store,
        settings=owned.repository,
        runtime=owned.runtime,
        delivery_ack=owned.ack,
    )
    completed = await recovered.resume_pending_ack(
        ManagedPendingAckRecovery(
            correlation_id="restart",
            source="discord",
            delivery_id="delivery",
            credential_ref="credential",
            expires_at=None,
            ack_secret_key="ack-secret-key",
            post_ack_settings_values=candidate.post_ack_settings_values,
            delivered=False,
            delivery_confirmed=True,
        )
    )

    assert completed.state.stage is ManagedTransactionStage.COMPLETED
    assert owned.ack.calls == 1
    assert "ack-secret-key" not in owned.store.values


@pytest.mark.asyncio
async def test_cleanup_conflict_rebases_same_ack_and_preserves_concurrent_state() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {
                "managed_connection": {
                    "pending_delivery_ack_source": None,
                    "pending_delivery_ack_delivery_id": None,
                    "pending_delivery_ack_managed_credential_ref": None,
                    "pending_delivery_ack_delivered": False,
                }
            }
        },
    )
    owned = harness(candidates=(candidate,))
    original = owned.repository.save
    conflicted = False

    async def conflict_once(request):  # noqa: ANN001, ANN202
        nonlocal conflicted
        if request.reason == "managed_delivery_ack_completed" and not conflicted:
            conflicted = True
            current = owned.repository.receipt.envelope
            owned.repository.receipt = SettingsCommitReceipt(
                replace(
                    current,
                    state=replace(
                        current.state,
                        managed_connection=replace(
                            current.state.managed_connection,
                            referral_id="concurrent",
                        ),
                    ),
                ),
                "concurrent",
                "concurrent",
                None,
            )
            return _revision_conflict_result()
        return await original(request)

    owned.repository.save = conflict_once
    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert result.settings_receipt.envelope.state.managed_connection.referral_id == "concurrent"
    assert owned.ack.calls == 1


@pytest.mark.asyncio
async def test_delivered_marker_conflict_rebases_same_ack_before_token_cleanup() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {
                "managed_connection": {
                    "pending_delivery_ack_source": None,
                    "pending_delivery_ack_delivery_id": None,
                    "pending_delivery_ack_managed_credential_ref": None,
                    "pending_delivery_ack_delivered": False,
                }
            }
        },
    )
    owned = harness(candidates=(candidate,))
    original = owned.repository.save
    conflicted = False

    async def conflict_marker_once(request):  # noqa: ANN001, ANN202
        nonlocal conflicted
        if request.reason == "managed_delivery_ack_delivered" and not conflicted:
            conflicted = True
            current = owned.repository.receipt.envelope
            owned.repository.receipt = SettingsCommitReceipt(
                replace(
                    current,
                    state=replace(
                        current.state,
                        managed_connection=replace(
                            current.state.managed_connection,
                            referral_id="marker-concurrent",
                        ),
                    ),
                ),
                "marker-concurrent",
                "marker-concurrent",
                None,
            )
            return _revision_conflict_result()
        return await original(request)

    owned.repository.save = conflict_marker_once
    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert result.settings_receipt.envelope.state.managed_connection.referral_id == (
        "marker-concurrent"
    )
    assert owned.ack.calls == 1
    assert "ack-secret-key" not in owned.store.values


@pytest.mark.asyncio
async def test_cleanup_conflict_rejects_different_ack_without_erasing_state_or_token() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    owned = harness(candidates=(candidate,))
    original = owned.repository.save

    async def replace_identity(request):  # noqa: ANN001, ANN202
        if request.reason == "managed_delivery_ack_completed":
            current = owned.repository.receipt.envelope
            owned.repository.receipt = SettingsCommitReceipt(
                replace(
                    current,
                    state=replace(
                        current.state,
                        managed_connection=replace(
                            current.state.managed_connection,
                            pending_delivery_ack_delivery_id="different",
                            pending_delivery_ack_managed_credential_ref="other",
                        ),
                    ),
                ),
                "different",
                "different",
                None,
            )
            owned.store.values["ack-secret-key"] = "concurrent-token"
            owned.store.values["ack-secret-key.delivered"] = encode_ack_delivery_confirmation(
                "discord", "different", "other"
            )
            return _revision_conflict_result()
        return await original(request)

    owned.repository.save = replace_identity
    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.CONFLICT
    assert result.state.detail_code == "pending_ack_identity_conflict"
    managed = owned.repository.receipt.envelope.state.managed_connection
    assert managed.pending_delivery_ack_delivery_id == "different"
    assert managed.pending_delivery_ack_managed_credential_ref == "other"
    assert owned.store.values["ack-secret-key"] == "concurrent-token"
    assert owned.store.values["ack-secret-key.delivered"] == encode_ack_delivery_confirmation(
        "discord", "different", "other"
    )


@pytest.mark.asyncio
async def test_token_replacement_exactly_before_compare_and_clear_is_never_deleted() -> None:
    metadata = ManagedKeyDeliveryAckMetadata("discord", "delivery", "credential", None, "ack-token")
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        post_ack_settings_values={
            "state": {"managed_connection": {"pending_delivery_ack_source": None}}
        },
    )
    owned = harness(candidates=(candidate,))

    def replace_before_clear(key: str) -> None:
        if key == "ack-secret-key":
            owned.store.values[key] = "concurrent-token"

    owned.store.compare_hook = replace_before_clear

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.CONFLICT
    assert result.state.detail_code == "ack_token_stale"
    assert owned.store.values["ack-secret-key"] == "concurrent-token"
    managed = owned.repository.receipt.envelope.state.managed_connection
    assert managed.pending_delivery_ack_delivery_id == "delivery"
    assert managed.pending_delivery_ack_delivered is True


@pytest.mark.asyncio
async def test_encrypted_file_compare_and_clear_detects_multiprocess_replacement(tmp_path) -> None:
    from puripuly_heart.core.storage.secrets import EncryptedFileSecretStore

    path = tmp_path / "secrets.json"
    passphrase = "cas-test-passphrase"
    key = "ack-token"
    store = EncryptedFileSecretStore(path, passphrase=passphrase)
    store.set(key, "old-token")
    adapter = SyncSecretStorePortAdapter(store)
    snapshot = await adapter.snapshot_secret(key)
    assert snapshot.revision is not None

    process = multiprocessing.get_context("spawn").Process(
        target=_replace_encrypted_secret,
        args=(str(path), passphrase, key, "new-token"),
    )
    process.start()
    await asyncio.to_thread(process.join, 15)
    assert process.exitcode == 0

    result = await adapter.compare_and_clear_secret(key, snapshot.revision)

    assert result.status == "stale"
    assert store.get(key) == "new-token"


@pytest.mark.asyncio
async def test_ambiguous_and_missing_credentials_are_rejected_deterministically() -> None:
    candidate = ManagedCredentialCandidate("a", "discord", "secret")
    ambiguous = harness(candidates=(candidate, ManagedCredentialCandidate("b", "discord", "other")))
    missing = harness(candidates=(ManagedCredentialCandidate("a", "qq", "secret"),))

    ambiguous_result = await ambiguous.coordinator.execute(request())
    missing_result = await missing.coordinator.execute(request())

    assert ambiguous_result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert ambiguous_result.state.detail_code == "credential_ambiguous"
    assert missing_result.state.detail_code == "credential_missing"
    assert ambiguous.store.events == missing.store.events == ["snapshot"]


@pytest.mark.asyncio
async def test_same_key_changed_request_is_conflict_and_never_replays_external_work() -> None:
    owned = harness()
    await owned.coordinator.execute(request())

    conflict = await owned.coordinator.execute(request(transaction_id="changed"))

    assert conflict.state.stage is ManagedTransactionStage.CONFLICT
    assert conflict.state.detail_code == "idempotency_conflict"
    assert owned.claim.calls == owned.ack.calls == 1


@pytest.mark.asyncio
async def test_rollback_failure_after_external_claim_requires_compensation() -> None:
    owned = harness()
    owned.repository.fail = "commit"
    owned.store.fail = "restore_secret"

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert result.rollback_succeeded is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["settings_rollback", "runtime_rollback"])
async def test_post_commit_rollback_failure_requires_compensation(failure: str) -> None:
    owned = harness()
    owned.runtime.fail = True
    if failure == "settings_rollback":
        owned.repository.fail = "rollback"
    else:
        owned.runtime.fail_all = True

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert result.state.detail_code == "runtime_apply_failed"
    assert result.rollback_succeeded is False
    assert owned.ack.calls == 0


@pytest.mark.asyncio
async def test_successful_rollback_receipt_and_runtime_become_authoritative_result() -> None:
    owned = harness()
    owned.runtime.fail = True

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert result.settings_receipt is not None
    assert result.settings_receipt.revision == "r3"
    assert result.state.settings_revision == "r3"
    assert result.runtime_status == "applied"
    assert tuple(receipt.revision for receipt in result.attempt_receipts) == ("r2", "r3")
    assert result.runtime_outcomes == (("r2", "failed"), ("r3", "applied"))


@pytest.mark.asyncio
async def test_production_repository_reports_post_load_revision_race_and_coordinator_compensates(
    tmp_path,
) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok

    class RacingPersistence(SettingsVNextCanonicalPersistenceAdapter):
        raced = False

        def persist_delta(self, path, **kwargs):  # noqa: ANN001, ANN003, ANN201
            if not self.raced:
                self.raced = True
                current = self.load_receipt(path, reason="race", correlation_id="race")
                changed = replace(
                    current.envelope,
                    state=replace(
                        current.envelope.state,
                        managed_connection=replace(
                            current.envelope.state.managed_connection,
                            referral_id="concurrent",
                        ),
                    ),
                )
                self.persist(path, changed)
            return super().persist_delta(path, **kwargs)

    persistence = RacingPersistence()
    repository = ProductionCanonicalSettingsRepository(persistence, path)
    before = await repository.load_receipt()
    store = Store()
    claim = Claim(
        ManagedClaimResult(
            "claimed",
            (ManagedCredentialCandidate("credential", "discord", "new-secret"),),
            "external",
        )
    )
    runtime = Runtime()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=Ack(),
    )

    result = await coordinator.execute(
        request(
            settings_values=persistence.values_for(before.envelope),
            expected_settings_revision=before.revision,
        )
    )

    assert result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert result.state.detail_code == "revision_conflict"
    assert result.diagnostics is not None
    assert result.diagnostics.code == "revision_conflict"
    assert result.rollback_succeeded is True
    assert store.values["managed"] == "old"
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_production_repository_returns_authoritative_revision_conflict_diagnostics(
    tmp_path,
) -> None:
    path = tmp_path / "settings.json"
    assert save_vnext_settings(path, AppSettingsVNext()).ok
    persistence = SettingsVNextCanonicalPersistenceAdapter()
    repository = ProductionCanonicalSettingsRepository(persistence, path)
    stale = await repository.load_receipt()
    changed = replace(
        stale.envelope,
        state=replace(
            stale.envelope.state,
            managed_connection=replace(
                stale.envelope.state.managed_connection,
                referral_id="authoritative",
            ),
        ),
    )
    persistence.persist(path, changed)

    result = await repository.save(
        SettingsCommitRequest(
            persistence.values_for(stale.envelope),
            stale.revision,
            "stale",
            "stale",
        )
    )

    authoritative = await repository.load_receipt()
    assert result.succeeded is False
    assert result.diagnostics is not None
    assert result.diagnostics.code == "revision_conflict"
    assert result.diagnostics.fields["expected_revision"] == stale.revision
    assert result.diagnostics.fields["authoritative_revision"] == authoritative.revision


@pytest.mark.asyncio
async def test_cancellation_during_rollback_restore_settles_exact_result() -> None:
    owned = harness()
    owned.repository.fail = "commit"
    entered = asyncio.Event()
    release = asyncio.Event()
    original = owned.store.restore_secret

    async def blocked(snapshot):  # noqa: ANN001, ANN202
        entered.set()
        await release.wait()
        return await original(snapshot)

    owned.store.restore_secret = blocked
    task = asyncio.create_task(owned.coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await owned.coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert settled.rollback_succeeded is True
    assert owned.store.values["managed"] == "old"


@pytest.mark.asyncio
async def test_stale_duplicate_result_cannot_replace_completed_result() -> None:
    owned = harness()
    completed = await owned.coordinator.execute(request())
    owned.claim.result = ManagedClaimResult("failed", (), None, "late_failure")

    duplicate = await owned.coordinator.execute(request())

    assert duplicate == completed
    assert duplicate.state.detail_code is None
    assert owned.claim.calls == 1


@pytest.mark.asyncio
async def test_cancellation_at_claim_is_terminal_and_does_not_continue() -> None:
    owned = harness()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_request):  # noqa: ANN001, ANN202
        entered.set()
        await release.wait()
        return owned.claim.result

    owned.claim.claim = blocked
    task = asyncio.create_task(owned.coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert owned.store.events == ["snapshot"]
    result = await owned.coordinator.execute(request())
    assert result.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED


@pytest.mark.asyncio
async def test_cancellation_settles_real_blocking_secret_thread_before_compensation() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingSecrets:
        def __init__(self) -> None:
            self.values = {"managed": "old"}

        def get(self, key):  # noqa: ANN001, ANN201
            return self.values.get(key)

        def set(self, key, value):  # noqa: ANN001
            entered.set()
            release.wait(timeout=5)
            self.values[key] = value

        def delete(self, key):  # noqa: ANN001
            self.values.pop(key, None)

    secrets = BlockingSecrets()
    port = SyncSecretStorePortAdapter(secrets)
    owned = harness()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=owned.claim,
        secrets=CanonicalSecretCommandService(port),
        secret_store=port,
        settings=owned.repository,
        runtime=owned.runtime,
        delivery_ack=owned.ack,
    )
    task = asyncio.create_task(coordinator.execute(request()))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert settled.state.detail_code == "cancelled_during_secret"
    assert settled.rollback_succeeded is True
    assert secrets.values["managed"] == "old"


@pytest.mark.asyncio
async def test_each_transaction_resolves_current_secret_authority_after_rebind() -> None:
    first = Store()
    second = Store()
    current = {"store": first}
    owned = harness()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=owned.claim,
        secrets=lambda: CanonicalSecretCommandService(current["store"]),
        secret_store=lambda: current["store"],
        settings=owned.repository,
        runtime=owned.runtime,
        delivery_ack=owned.ack,
    )
    current["store"] = second

    result = await coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert first.values["managed"] == "old"
    assert second.values["managed"] == "new-secret"
    assert first.events == []
    assert second.events[:2] == ["snapshot", "secret"]


@pytest.mark.asyncio
async def test_canonical_production_rebind_publishes_authority_used_by_transaction(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = InMemorySecretStore()
    initial_port = SyncSecretStorePortAdapter(initial)
    runtime = Runtime()

    class Host:
        bound = None

        async def rebind_secret_store(self, secrets, _receipt):  # noqa: ANN001
            self.bound = secrets

    host = Host()
    canonical = CanonicalCommandComposition(
        settings_commands=object(),  # type: ignore[arg-type]
        operational_commands=object(),  # type: ignore[arg-type]
        secret_commands=CanonicalSecretCommandService(initial_port),
        runtime_host=host,
        runtime_apply=runtime,  # type: ignore[arg-type]
        activated_surfaces=frozenset(),
        _secrets=initial,
        _state_path=tmp_path / "settings.json",
        _secret_port=initial_port,
    )
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            secrets=replace(
                settings.intent.secrets,
                backend="encrypted_file",
                encrypted_file_path=str(tmp_path / "managed-secrets.json"),
            ),
        ),
    )
    receipt = SettingsCommitReceipt(settings, "rebind", "rebind", "rebind")
    monkeypatch.setenv("PURIPULY_HEART_SECRETS_PASSPHRASE", "managed-test-passphrase")

    rebind_error = await canonical.try_rebind_secrets_from_intent(receipt)
    owned = harness()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=owned.claim,
        secrets=lambda: canonical.secret_commands,
        secret_store=lambda: canonical._secret_port,
        settings=owned.repository,
        runtime=runtime,
        delivery_ack=owned.ack,
    )
    result = await coordinator.execute(request())

    assert rebind_error is None
    assert host.bound is canonical._secrets
    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert initial.get("managed") is None
    assert canonical._secrets.get("managed") == "new-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("await_name", "terminal"),
    [
        ("claim", ManagedTransactionStage.COMPENSATION_REQUIRED),
        ("snapshot", ManagedTransactionStage.CANCELLED),
        ("secret", ManagedTransactionStage.COMPENSATION_REQUIRED),
        ("load", ManagedTransactionStage.CANCELLED),
        ("commit", ManagedTransactionStage.COMPENSATION_REQUIRED),
        ("apply", ManagedTransactionStage.COMPENSATION_REQUIRED),
        ("ack", ManagedTransactionStage.COMPLETED),
    ],
)
async def test_cancellation_settles_at_every_transaction_await(
    await_name, terminal
) -> None:  # noqa: ANN001
    owned = harness()
    entered = asyncio.Event()
    release = asyncio.Event()
    target = {
        "claim": (owned.claim, "claim"),
        "snapshot": (owned.store, "snapshot_secret"),
        "secret": (owned.store, "set_secret"),
        "load": (owned.repository, "load_receipt"),
        "commit": (owned.repository, "save"),
        "apply": (owned.runtime, "apply_runtime"),
        "ack": (owned.ack, "acknowledge"),
    }[await_name]
    original = getattr(*target)
    first = True

    async def blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    setattr(target[0], target[1], blocked)
    task = asyncio.create_task(owned.coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await owned.coordinator.execute(request())
    assert settled.state.stage is terminal
    assert owned.claim.calls <= 1
    assert owned.ack.calls <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "ordinal"),
    [("snapshot_secret", 2), ("set_secret", 2), ("snapshot_secret", 3), ("set_secret", 3)],
)
async def test_cancellation_settles_during_ack_and_auxiliary_secret_awaits(
    operation: str,
    ordinal: int,
) -> None:
    metadata = ManagedKeyDeliveryAckMetadata(
        source="discord",
        delivery_id="delivery",
        managed_credential_ref="credential",
        expires_at=None,
        delivery_ack_token="ack-token",
    )
    candidate = ManagedCredentialCandidate(
        "credential",
        "discord",
        "managed-secret",
        metadata,
        ack_secret_key="ack-secret-key",
        auxiliary_secrets=(("managed-user", "user"),),
    )
    owned = harness(candidates=(candidate,))
    entered = asyncio.Event()
    release = asyncio.Event()
    original = getattr(owned.store, operation)
    calls = 0

    async def blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == ordinal:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    setattr(owned.store, operation, blocked)
    task = asyncio.create_task(owned.coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await owned.coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.COMPENSATION_REQUIRED
    assert owned.ack.calls == 0


def test_transaction_dtos_are_immutable_and_hide_raw_credentials() -> None:
    candidate = ManagedCredentialCandidate("credential", "discord", "raw-secret")
    transaction = request()

    with pytest.raises(Exception):
        transaction.settings_values["x"] = "y"  # type: ignore[index]
    assert "raw-secret" not in repr(candidate)
    assert "raw-secret" not in repr(ManagedClaimResult("claimed", (candidate,)))


@pytest.mark.asyncio
async def test_production_qq_claim_port_returns_typed_artifact_without_local_mutation() -> None:
    store = Store()
    store.values.clear()

    class State:
        installation_id = "installation"
        local_managed_claim_sources = ()
        active_managed_credential_ref = None
        active_managed_expires_at = None

    class Client:
        async def assert_qq_managed_identity(self, _request):  # noqa: ANN001, ANN201
            return QqManagedAssertionResult(
                True,
                "managed-secret",
                QqManagedEntitlementSnapshot("subject", "credential", "expiry", "user"),
                None,
                None,
                None,
                None,
            )

    state = State()

    class Release:
        managed_state = state
        client = Client()

        async def claim_qq_managed_key(self, **_kwargs):  # noqa: ANN003, ANN201
            asserted = await self.client.assert_qq_managed_identity(object())
            return ManagedProviderClaimResult(
                "claimed",
                "qq",
                asserted.managed_secret_key,
                asserted.entitlement.managed_credential_ref,
                asserted.entitlement.expires_at,
                asserted.entitlement.openrouter_user_id,
            )

    release = Release()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    owner = ProductionManagedClaimOwner(
        Host(),
        store,
        ProductionManagedAuthenticationBrowser(OAuthRuntime()),
        lambda _available: None,
    )
    result = await owner.claim(
        request(
            claim_source="qq",
            local_secret_key="openrouter_managed_qq_api_key",
            claim_input=ManagedClaimInput(
                identity="42",
                credential="qq-secret",
                asserted_at="2026-01-01T00:00:00Z",
            ),
        )
    )

    assert result.status == "claimed"
    assert result.candidates[0].credential_ref == "credential"
    assert result.candidates[0].settings_values["state"]["managed_connection"][  # type: ignore[index]
        "local_managed_claim_sources"
    ] == (
        "qq",
    )
    assert state.local_managed_claim_sources == ()
    assert store.values == {}
    assert "managed-secret" not in repr(result)
    assert "qq-secret" not in repr(result)


@pytest.mark.asyncio
async def test_coordinator_applies_provider_identity_delta_only_after_claim() -> None:
    class State:
        installation_id = ""
        local_managed_claim_sources = ()
        active_managed_credential_ref = None
        active_managed_expires_at = None

    state = State()
    sync_secrets = InMemorySecretStore()
    prepared = prepare_managed_identity_bundle(state, sync_secrets)

    class Release:
        managed_state = state
        client = object()

        async def claim_discord_managed_key(self, **_kwargs):  # noqa: ANN003, ANN201
            assert sync_secrets._items == {}
            assert state.installation_id == ""
            return ManagedProviderClaimResult(
                "claimed",
                "discord",
                "managed-secret",
                "credential-ref",
                identity=prepared,
            )

    release = Release()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    secret_port = SyncSecretStorePortAdapter(sync_secrets)
    repository = Repository()
    runtime = Runtime()
    ack = Ack()
    claim = ProductionManagedClaimOwner(
        Host(),
        secret_port,
        ProductionManagedAuthenticationBrowser(OAuthRuntime()),
        lambda _available: None,
    )
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=CanonicalSecretCommandService(secret_port),
        secret_store=secret_port,
        settings=repository,
        runtime=runtime,
        delivery_ack=ack,
    )

    result = await coordinator.execute(request(local_secret_key="openrouter_managed_api_key"))

    assert result.state.stage is ManagedTransactionStage.COMPLETED
    assert sync_secrets.get("openrouter_managed_api_key") == "managed-secret"
    assert sync_secrets.get("managed_device_private_key") is not None
    managed_values = repository.requests[0].values["state"]["managed_connection"]  # type: ignore[index]
    assert managed_values["installation_id"] == prepared.bundle.installation_id  # type: ignore[index]
    assert managed_values["active_managed_credential_ref"] == "credential-ref"  # type: ignore[index]


@pytest.mark.asyncio
async def test_binding_mismatch_replacement_identity_is_applied_only_by_coordinator() -> None:
    replacement = prepare_replacement_managed_identity_bundle()
    sync_secrets = InMemorySecretStore()
    secret_port = SyncSecretStorePortAdapter(sync_secrets)

    class State:
        release_token = "release-token"

    class Release:
        managed_state = State()
        client = object()

        async def claim_release_token_managed_key(self):  # noqa: ANN201
            return ManagedProviderClaimResult(
                "failed",
                "release_token",
                detail_code="binding_mismatch",
                release_result=ManagedOpenRouterReleaseResult(
                    ManagedOpenRouterReleaseBehavior.RESTART,
                    "managed_release.restart",
                ),
                clear_temporary_state=True,
                identity=replacement,
            )

    release = Release()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    repository = Repository()
    current = repository.receipt.envelope
    repository.receipt = SettingsCommitReceipt(
        replace(
            current,
            state=replace(
                current.state,
                managed_connection=replace(
                    current.state.managed_connection,
                    installation_id="invalid-installation",
                    release_token="release-token",
                    verified_hardware_hash="hash",
                    verified_hardware_hash_salt_version=1,
                ),
            ),
        ),
        "r1",
        "before",
        None,
    )
    claim = ProductionManagedClaimOwner(
        Host(),
        secret_port,
        ProductionManagedAuthenticationBrowser(OAuthRuntime()),
        lambda _available: None,
    )
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=CanonicalSecretCommandService(secret_port),
        secret_store=secret_port,
        settings=repository,
        runtime=Runtime(),
        delivery_ack=Ack(),
    )

    result = await coordinator.execute(
        request(
            claim_source="release_token",
            local_secret_key="openrouter_managed_api_key",
            settings_values=serialization.to_dict(repository.receipt.envelope),
        )
    )

    assert result.state.stage is ManagedTransactionStage.TERMINAL_FAILURE
    managed = repository.receipt.envelope.state.managed_connection
    assert managed.installation_id == replacement.bundle.installation_id
    assert managed.release_token is None
    assert sync_secrets.get("managed_device_private_key") is not None
    assert sync_secrets.get("managed_device_public_key") == replacement.bundle.device_public_key
    assert release.managed_state.release_token == "release-token"


def _replacement_failure_claim(secret_count: int = 1) -> Claim:
    return Claim(
        ManagedClaimResult(
            "failed",
            detail_code="binding_mismatch",
            failure_settings_values={
                "state": {"managed_connection": {"installation_id": "replacement"}}
            },
            failure_auxiliary_secrets=tuple(
                (
                    "identity-secret" if index == 0 else f"identity-secret-{index}",
                    "replacement-secret" if index == 0 else f"replacement-secret-{index}",
                )
                for index in range(secret_count)
            ),
        )
    )


@pytest.mark.asyncio
async def test_replacement_identity_cancellation_settles_real_blocking_secret_write() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingSecrets:
        def __init__(self) -> None:
            self.values = {"identity-secret": "old-secret"}

        def get(self, key):  # noqa: ANN001, ANN201
            return self.values.get(key)

        def set(self, key, value):  # noqa: ANN001
            entered.set()
            release.wait(timeout=5)
            self.values[key] = value

        def delete(self, key):  # noqa: ANN001
            self.values.pop(key, None)

        def compare_and_clear(self, key, expected_revision):  # noqa: ANN001, ANN201
            value = self.values.get(key)
            if value is None:
                return "absent"
            if _test_secret_revision(value) != expected_revision:
                return "stale"
            self.values.pop(key, None)
            return "cleared"

    secrets = BlockingSecrets()
    secret_port = SyncSecretStorePortAdapter(secrets)
    repository = Repository()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(),
        secrets=CanonicalSecretCommandService(secret_port),
        secret_store=secret_port,
        settings=repository,
        runtime=Runtime(),
        delivery_ack=Ack(),
    )
    task = asyncio.create_task(coordinator.execute(request()))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.CANCELLED
    assert settled.rollback_succeeded is True
    assert secrets.values["identity-secret"] == "old-secret"
    assert settled.state.stage is not ManagedTransactionStage.NEW


@pytest.mark.asyncio
async def test_replacement_identity_cancellation_observes_late_commit_and_rolls_back() -> None:
    repository = Repository()
    entered = asyncio.Event()
    release = asyncio.Event()
    original = repository.save
    first = True

    async def late_commit(request):  # noqa: ANN001, ANN202
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return await original(request)

    repository.save = late_commit
    store = Store()
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(),
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=Runtime(),
        delivery_ack=Ack(),
    )
    task = asyncio.create_task(coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.CANCELLED
    assert settled.rollback_succeeded is True
    assert tuple(receipt.revision for receipt in settled.attempt_receipts) == ("r2", "r3")
    assert repository.receipt.envelope.state.managed_connection.installation_id == ""
    assert store.values.get("identity-secret") is None


@pytest.mark.asyncio
async def test_replacement_identity_cancellation_observes_late_apply_and_restores_runtime() -> None:
    repository = Repository()
    store = Store()
    runtime = Runtime()
    entered = asyncio.Event()
    release = asyncio.Event()
    original = runtime.apply_runtime
    first = True

    async def late_apply(request):  # noqa: ANN001, ANN202
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return await original(request)

    runtime.apply_runtime = late_apply
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(),
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=Ack(),
    )
    task = asyncio.create_task(coordinator.execute(request()))
    await entered.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.CANCELLED
    assert settled.rollback_succeeded is True
    assert settled.runtime_outcomes == (("r2", "applied"), ("r3", "applied"))
    assert repository.receipt.envelope.state.managed_connection.installation_id == ""
    assert store.values.get("identity-secret") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_await", ["commit", "apply", "restore"])
async def test_replacement_identity_repeated_cancellation_settles_every_rollback_await(
    rollback_await: str,
) -> None:
    repository = Repository()
    store = Store()
    runtime = Runtime()
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()
    rollback_entered = asyncio.Event()
    release_rollback = asyncio.Event()
    original_save = repository.save
    original_apply = runtime.apply_runtime
    original_restore = store.restore_secret
    apply_calls = 0

    async def controlled_save(request):  # noqa: ANN001, ANN202
        if (
            rollback_await == "commit"
            and request.reason == "managed_release_claim_reconciliation_rollback"
        ):
            rollback_entered.set()
            await release_rollback.wait()
        return await original_save(request)

    async def controlled_apply(request):  # noqa: ANN001, ANN202
        nonlocal apply_calls
        apply_calls += 1
        if apply_calls == 1:
            apply_entered.set()
            await release_apply.wait()
        elif rollback_await == "apply":
            rollback_entered.set()
            await release_rollback.wait()
        return await original_apply(request)

    async def controlled_restore(snapshot):  # noqa: ANN001, ANN202
        if rollback_await == "restore":
            rollback_entered.set()
            await release_rollback.wait()
        return await original_restore(snapshot)

    repository.save = controlled_save
    runtime.apply_runtime = controlled_apply
    store.restore_secret = controlled_restore
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(),
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=Ack(),
    )
    task = asyncio.create_task(coordinator.execute(request()))
    await apply_entered.wait()
    task.cancel()
    release_apply.set()
    await rollback_entered.wait()
    task.cancel()
    release_rollback.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.CANCELLED
    assert settled.state.cancellation_count == 2
    assert settled.rollback_succeeded is True
    assert tuple(receipt.revision for receipt in settled.attempt_receipts) == ("r2", "r3")
    assert settled.runtime_outcomes == (("r2", "applied"), ("r3", "applied"))
    assert repository.receipt.envelope.state.managed_connection.installation_id == ""
    assert store.values.get("identity-secret") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rollback_await", "restore_ordinal"),
    [("commit", None), ("apply", None), ("restore", 1), ("restore", 2)],
)
async def test_first_cancellation_during_ordinary_failure_rollback_is_propagated(
    rollback_await: str,
    restore_ordinal: int | None,
) -> None:
    repository = Repository()
    store = Store()
    runtime = Runtime()
    runtime.fail = True
    rollback_entered = asyncio.Event()
    release_rollback = asyncio.Event()
    original_save = repository.save
    original_apply = runtime.apply_runtime
    original_restore = store.restore_secret
    apply_calls = 0
    restore_calls = 0

    async def controlled_save(request):  # noqa: ANN001, ANN202
        if (
            rollback_await == "commit"
            and request.reason == "managed_release_claim_reconciliation_rollback"
        ):
            rollback_entered.set()
            await release_rollback.wait()
        return await original_save(request)

    async def controlled_apply(request):  # noqa: ANN001, ANN202
        nonlocal apply_calls
        apply_calls += 1
        if rollback_await == "apply" and apply_calls == 2:
            rollback_entered.set()
            await release_rollback.wait()
        return await original_apply(request)

    async def controlled_restore(snapshot):  # noqa: ANN001, ANN202
        nonlocal restore_calls
        restore_calls += 1
        if rollback_await == "restore" and restore_calls == restore_ordinal:
            rollback_entered.set()
            await release_rollback.wait()
        return await original_restore(snapshot)

    repository.save = controlled_save
    runtime.apply_runtime = controlled_apply
    store.restore_secret = controlled_restore
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(secret_count=2),
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=Ack(),
    )
    task = asyncio.create_task(coordinator.execute(request()))
    await rollback_entered.wait()
    task.cancel()
    release_rollback.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(request())
    assert settled.state.stage is ManagedTransactionStage.CANCELLED
    assert settled.state.cancellation_count == 1
    assert settled.rollback_succeeded is True
    assert tuple(receipt.revision for receipt in settled.attempt_receipts) == ("r2", "r3")
    assert settled.runtime_outcomes == (("r2", "failed"), ("r3", "applied"))
    assert repository.receipt.envelope.state.managed_connection.installation_id == ""
    assert store.values.get("identity-secret") is None
    assert store.values.get("identity-secret-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_failure", ["commit", "apply", "restore"])
async def test_cancellation_with_incomplete_rollback_routes_to_reconciliation(
    rollback_failure: str,
) -> None:
    repository = Repository()
    store = Store()
    runtime = Runtime()
    runtime.fail = True
    rollback_entered = asyncio.Event()
    release_rollback = asyncio.Event()
    original_save = repository.save
    original_apply = runtime.apply_runtime
    original_restore = store.restore_secret
    apply_calls = 0

    async def failing_save(request):  # noqa: ANN001, ANN202
        if (
            rollback_failure == "commit"
            and request.reason == "managed_release_claim_reconciliation_rollback"
        ):
            rollback_entered.set()
            await release_rollback.wait()
            raise RuntimeError("rollback commit failed")
        return await original_save(request)

    async def failing_apply(request):  # noqa: ANN001, ANN202
        nonlocal apply_calls
        apply_calls += 1
        if rollback_failure == "apply" and apply_calls == 2:
            rollback_entered.set()
            await release_rollback.wait()
            raise RuntimeError("rollback apply failed")
        return await original_apply(request)

    async def failing_restore(snapshot):  # noqa: ANN001, ANN202
        if rollback_failure == "restore":
            rollback_entered.set()
            await release_rollback.wait()
            raise RuntimeError("secret restore failed")
        return await original_restore(snapshot)

    repository.save = failing_save
    runtime.apply_runtime = failing_apply
    store.restore_secret = failing_restore
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=_replacement_failure_claim(),
        secrets=CanonicalSecretCommandService(store),
        secret_store=store,
        settings=repository,
        runtime=runtime,
        delivery_ack=Ack(),
    )
    transaction = request()
    task = asyncio.create_task(coordinator.execute(transaction))
    await rollback_entered.wait()
    task.cancel()
    release_rollback.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    settled = await coordinator.execute(transaction)
    retried = await coordinator.retry_idempotency(transaction.idempotency_key)
    assert settled.state.stage is ManagedTransactionStage.RECONCILIATION_REQUIRED
    assert settled.state.cancellation_count == 1
    assert settled.rollback_succeeded is False
    assert settled.state.detail_code.endswith("_rollback_incomplete")
    assert settled.diagnostics is not None
    assert settled.diagnostics.code == settled.state.detail_code
    assert retried == settled


@pytest.mark.asyncio
async def test_discord_binding_mismatch_replacement_is_applied_by_production_coordinator() -> None:
    replacement = prepare_replacement_managed_identity_bundle()
    sync_secrets = InMemorySecretStore()
    secret_port = SyncSecretStorePortAdapter(sync_secrets)

    class State:
        local_managed_claim_sources = ()

    class Release:
        managed_state = State()
        client = object()

        async def claim_discord_managed_key(self, **_kwargs):  # noqa: ANN003, ANN201
            return ManagedProviderClaimResult(
                "failed",
                "discord",
                detail_code="discord_auth.error.retry",
                release_result=ManagedOpenRouterReleaseResult(
                    ManagedOpenRouterReleaseBehavior.RESTART,
                    "managed_release.restart",
                ),
                clear_temporary_state=True,
                identity=replacement,
            )

    release = Release()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    repository = Repository()
    claim = ProductionManagedClaimOwner(
        Host(),
        secret_port,
        ProductionManagedAuthenticationBrowser(OAuthRuntime()),
        lambda _available: None,
    )
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=CanonicalSecretCommandService(secret_port),
        secret_store=secret_port,
        settings=repository,
        runtime=Runtime(),
        delivery_ack=Ack(),
    )

    result = await coordinator.execute(
        request(
            claim_input=ManagedClaimInput(referral_id="referral"),
            settings_values=serialization.to_dict(repository.receipt.envelope),
        )
    )

    assert result.state.stage is ManagedTransactionStage.TERMINAL_FAILURE
    assert repository.receipt.envelope.state.managed_connection.installation_id == (
        replacement.bundle.installation_id
    )
    assert sync_secrets.get("managed_device_public_key") == replacement.bundle.device_public_key
    assert release.managed_state.local_managed_claim_sources == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "message_key", "clear_state", "retry_after_ms"),
    [
        (ManagedOpenRouterReleaseBehavior.RESTART, "managed_release.restart", True, None),
        (ManagedOpenRouterReleaseBehavior.STOP, "managed_release.stop", True, None),
        (
            ManagedOpenRouterReleaseBehavior.RETRY,
            "managed_release.retry_after_ms",
            False,
            9000,
        ),
    ],
)
async def test_release_transaction_port_preserves_claim_outcome_and_canonical_clear(
    behavior,
    message_key,
    clear_state,
    retry_after_ms,
) -> None:  # noqa: ANN001
    outcome = ManagedOpenRouterReleaseResult(
        behavior,
        message_key,
        diagnostics=ManagedOpenRouterReleaseDiagnostics(
            operation="issue",
            code="classified",
            error_class=(
                "retryable" if behavior is ManagedOpenRouterReleaseBehavior.RETRY else "terminal"
            ),
        ),
        retry_after_ms=retry_after_ms,
    )
    repository = Repository()
    current = repository.receipt.envelope
    repository.receipt = SettingsCommitReceipt(
        replace(
            current,
            state=replace(
                current.state,
                managed_connection=replace(
                    current.state.managed_connection,
                    release_token="release-token",
                    release_token_expires_at="expiry",
                    verified_hardware_hash="hash",
                    verified_hardware_hash_salt_version=1,
                ),
            ),
        ),
        "r1",
        "before",
        None,
    )
    claim = Claim(
        ManagedClaimResult(
            "failed",
            detail_code="classified",
            failure_settings_values=(
                {
                    "state": {
                        "managed_connection": {
                            "release_token": None,
                            "release_token_expires_at": None,
                            "verified_hardware_hash": None,
                            "verified_hardware_hash_salt_version": None,
                        }
                    }
                }
                if clear_state
                else {}
            ),
            release_outcome=outcome,
        )
    )
    store = Store()
    commands = CanonicalSecretCommandService(store)
    coordinator = ManagedCanonicalTransactionCoordinator(
        authentication_owner=object(),
        claim=claim,
        secrets=commands,
        secret_store=store,
        settings=repository,
        runtime=Runtime(),
        delivery_ack=Ack(),
    )
    port = ProductionManagedReleaseTransactionPort(
        coordinator,
        repository,
        type("Persistence", (), {"values_for": staticmethod(serialization.to_dict)})(),
        lambda: commands,
    )

    class Release:
        managed_state = type("State", (), {"release_token": "release-token"})()
        recorded = []

        def record_transaction_outcome(self, result):  # noqa: ANN001
            self.recorded.append(result)

    release = Release()

    result = await port.ensure_key_for_llm_start(release, available_api_key=None)

    assert result == outcome
    assert release.recorded == [outcome]
    managed = repository.receipt.envelope.state.managed_connection
    assert managed.release_token == (None if clear_state else "release-token")
    assert managed.verified_hardware_hash == (None if clear_state else "hash")


@pytest.mark.asyncio
async def test_active_production_ack_owner_reuses_runtime_release_state_and_identity() -> None:
    store = Store()
    store.values["openrouter_managed_delivery_ack_token"] = "ack-token"

    class State:
        pending_delivery_ack_source = "discord"
        pending_delivery_ack_delivery_id = "delivery"
        pending_delivery_ack_managed_credential_ref = "credential"
        pending_delivery_ack_expires_at = None
        persists = 0

        def persist(self) -> None:
            self.persists += 1

    class Client:
        calls = 0

        async def acknowledge_managed_key_delivery(self, request):  # noqa: ANN001, ANN201
            self.calls += 1
            assert request.delivery_ack_token == "ack-token"
            return ManagedKeyDeliveryAckResult(True, "acknowledged")

    state = State()
    client = Client()
    release = type("Release", (), {"managed_state": state, "client": client})()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            return release

    owner = ProductionManagedDeliveryAckOwner(Host(), store)
    mismatched = await owner.acknowledge(
        type(
            "Request",
            (),
            {
                "source": "discord",
                "delivery_id": "stale",
                "credential_ref": "credential",
            },
        )()
    )
    result = await owner.acknowledge(
        type(
            "Request",
            (),
            {
                "source": "discord",
                "delivery_id": "delivery",
                "credential_ref": "credential",
            },
        )()
    )

    assert mismatched == ManagedAckResult(False, "ack_identity_mismatch")
    assert result == ManagedAckResult(True, "acknowledged")
    assert client.calls == 1
    assert state.persists == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["retryable", "managed_release_unavailable", "token_read_failed"],
)
async def test_production_ack_outcomes_preserve_retryable_transaction(status: str) -> None:
    owned = harness()
    owned.store.values["openrouter_managed_delivery_ack_token"] = "ack-secret"

    class State:
        pending_delivery_ack_source = "discord"
        pending_delivery_ack_delivery_id = "delivery-1"
        pending_delivery_ack_managed_credential_ref = "credential-1"
        pending_delivery_ack_expires_at = None

    class Client:
        async def acknowledge_managed_key_delivery(self, _request):  # noqa: ANN001, ANN201
            return ManagedKeyDeliveryAckResult(False, "retryable")

    release = type("Release", (), {"managed_state": State(), "client": Client()})()

    class Host:
        async def resolve_managed_release_service(self):  # noqa: ANN201
            if status == "managed_release_unavailable":
                return None
            return release

    if status == "token_read_failed":

        async def failed_get(_key):  # noqa: ANN001, ANN202
            raise RuntimeError

        owned.store.get_secret = failed_get
    owned.coordinator._delivery_ack = ProductionManagedDeliveryAckOwner(Host(), owned.store)

    result = await owned.coordinator.execute(request())

    assert result.state.stage is ManagedTransactionStage.RETRY_ACK
    assert result.state.detail_code == status
    assert result.state.retry_supported is True
    assert owned.store.values["openrouter_managed_delivery_ack_token"] == "ack-secret"
