from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from puripuly_heart.app.ports.application_settings import ClearSecretCommand, SetSecretCommand
from puripuly_heart.app.ports.broker_client import ManagedKeyDeliveryAckMetadata
from puripuly_heart.app.ports.owned_async import OwnedFailure, settle_owned
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyPort, RuntimeApplyRequest
from puripuly_heart.app.ports.secret_store import SecretSnapshot, SecretStorePort
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitReceipt,
    SettingsCommitRequest,
    SettingsRepositoryPort,
)
from puripuly_heart.app.services.canonical_secret_commands import CanonicalSecretCommandService
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_TRANSACTION,
    DIAGNOSTIC_VISIBILITY_BASIC,
    ErrorDiagnostics,
)


class ManagedTransactionStage(str, Enum):
    NEW = "new"
    CLAIMED = "claimed"
    SECRET_MUTATED = "secret_mutated"
    COMMITTED = "committed"
    APPLIED = "applied"
    COMPLETED = "completed"
    CONFLICT = "conflict"
    RETRY_ACK = "retry_ack"
    RETRY_CLEANUP = "retry_cleanup"
    ROLLED_BACK = "rolled_back"
    COMPENSATION_REQUIRED = "compensation_required"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ManagedTransactionRequest:
    transaction_id: str
    idempotency_key: str
    correlation_id: str
    claim_source: str
    local_secret_key: str
    settings_values: Mapping[str, object]
    expected_settings_revision: str
    reason: str | None = None
    claim_input: ManagedClaimInput | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.transaction_id,
                self.idempotency_key,
                self.correlation_id,
                self.claim_source,
                self.local_secret_key,
                self.expected_settings_revision,
            )
        ):
            raise ValueError("managed transaction identity and revision must be non-empty")
        object.__setattr__(self, "settings_values", _freeze(self.settings_values))


@dataclass(frozen=True, slots=True)
class ManagedCredentialCandidate:
    credential_ref: str
    source: str
    secret_value: str = field(repr=False)
    delivery_ack: ManagedKeyDeliveryAckMetadata | None = field(default=None, repr=False)
    settings_values: Mapping[str, object] = field(default_factory=dict, repr=False)
    ack_secret_key: str | None = None
    referral_bonus_applied: bool = False
    auxiliary_secrets: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    clear_secret_keys: tuple[str, ...] = ()
    post_ack_settings_values: Mapping[str, object] = field(default_factory=dict, repr=False)
    ack_delivered_settings_values: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings_values", _freeze(self.settings_values))
        object.__setattr__(self, "auxiliary_secrets", tuple(self.auxiliary_secrets))
        object.__setattr__(self, "clear_secret_keys", tuple(self.clear_secret_keys))
        object.__setattr__(
            self,
            "post_ack_settings_values",
            _freeze(self.post_ack_settings_values),
        )
        object.__setattr__(
            self,
            "ack_delivered_settings_values",
            _freeze(self.ack_delivered_settings_values),
        )


@dataclass(frozen=True, slots=True)
class ManagedClaimInput:
    referral_id: str | None = None
    identity: str | None = field(default=None, repr=False)
    credential: str | None = field(default=None, repr=False)
    asserted_at: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedClaimResult:
    status: str
    candidates: tuple[ManagedCredentialCandidate, ...] = ()
    external_claim_ref: str | None = None
    detail_code: str | None = None
    failure_settings_values: Mapping[str, object] = field(default_factory=dict, repr=False)
    release_outcome: object | None = field(default=None, repr=False)
    failure_auxiliary_secrets: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    failure_clear_secret_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "failure_settings_values", _freeze(self.failure_settings_values))
        object.__setattr__(self, "failure_auxiliary_secrets", tuple(self.failure_auxiliary_secrets))
        object.__setattr__(self, "failure_clear_secret_keys", tuple(self.failure_clear_secret_keys))


@dataclass(frozen=True, slots=True)
class ManagedAckRequest:
    transaction_id: str
    correlation_id: str
    source: str
    delivery_id: str
    credential_ref: str
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ManagedAckResult:
    succeeded: bool
    status: str


@dataclass(frozen=True, slots=True)
class ManagedPendingAckRecovery:
    correlation_id: str
    source: str
    delivery_id: str
    credential_ref: str
    expires_at: str | None
    ack_secret_key: str
    post_ack_settings_values: Mapping[str, object]
    delivered: bool = False
    delivery_confirmed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "post_ack_settings_values",
            _freeze(self.post_ack_settings_values),
        )


class ManagedClaimPort(Protocol):
    async def claim(self, request: ManagedTransactionRequest) -> ManagedClaimResult: ...


class ManagedDeliveryAckPort(Protocol):
    async def acknowledge(self, request: ManagedAckRequest) -> ManagedAckResult: ...


@dataclass(frozen=True, slots=True)
class ManagedTransactionState:
    transaction_id: str
    idempotency_key: str
    correlation_id: str
    request_fingerprint: str
    stage: ManagedTransactionStage
    claim_source: str
    external_claim_ref: str | None = None
    credential_ref: str | None = None
    secret_key: str | None = None
    secret_revision: str | None = None
    settings_revision: str | None = None
    ack_delivery_id: str | None = None
    detail_code: str | None = None
    retry_supported: bool = False
    claim_attempts: int = 0
    ack_attempts: int = 0
    referral_bonus_applied: bool = False
    cancellation_count: int = 0


@dataclass(frozen=True, slots=True)
class ManagedTransactionResult:
    state: ManagedTransactionState
    secret_metadata: tuple[str, str | None] | None = None
    settings_receipt: SettingsCommitReceipt | None = None
    prior_settings_receipt: SettingsCommitReceipt | None = None
    runtime_status: str | None = None
    rollback_succeeded: bool | None = None
    secret_receipts: tuple[tuple[str, str | None], ...] = ()
    diagnostics: ErrorDiagnostics | None = None
    attempt_receipts: tuple[SettingsCommitReceipt, ...] = ()
    runtime_outcomes: tuple[tuple[str, str], ...] = ()
    release_outcome: object | None = field(default=None, repr=False)


@dataclass(slots=True)
class _Execution:
    request: ManagedTransactionRequest
    fingerprint: str
    state: ManagedTransactionState
    candidate: ManagedCredentialCandidate | None = field(default=None, repr=False)
    secret_snapshot: SecretSnapshot | None = field(default=None, repr=False)
    ack_secret_snapshot: SecretSnapshot | None = field(default=None, repr=False)
    ack_confirmation_snapshot: SecretSnapshot | None = field(default=None, repr=False)
    auxiliary_secret_snapshots: list[SecretSnapshot] = field(default_factory=list, repr=False)
    failure_secret_snapshots: list[SecretSnapshot] = field(default_factory=list, repr=False)
    secret_store: SecretStorePort | None = field(default=None, repr=False)
    secret_commands: CanonicalSecretCommandService | None = field(default=None, repr=False)
    before_receipt: SettingsCommitReceipt | None = None
    receipt: SettingsCommitReceipt | None = None
    runtime_status: str | None = None
    terminal: ManagedTransactionResult | None = None
    secret_receipts: list[tuple[str, str | None]] = field(default_factory=list, repr=False)
    attempt_receipts: list[SettingsCommitReceipt] = field(default_factory=list, repr=False)
    runtime_outcomes: list[tuple[str, str]] = field(default_factory=list)
    ack_delivered_persisted: bool = False
    ack_delivery_confirmed: bool = False
    release_outcome: object | None = field(default=None, repr=False)


class ManagedCanonicalTransactionCoordinator:
    def __init__(
        self,
        *,
        authentication_owner: object,
        claim: ManagedClaimPort,
        secrets: CanonicalSecretCommandService | Callable[[], CanonicalSecretCommandService],
        secret_store: SecretStorePort | Callable[[], SecretStorePort],
        settings: SettingsRepositoryPort,
        runtime: RuntimeApplyPort,
        delivery_ack: ManagedDeliveryAckPort,
    ) -> None:
        self.authentication_owner = authentication_owner
        self._claim = claim
        self._secrets_source = secrets
        self._secret_store_source = secret_store
        self._settings = settings
        self._runtime = runtime
        self._delivery_ack = delivery_ack
        self._executions: dict[str, _Execution] = {}
        self._lock = asyncio.Lock()

    async def execute(self, request: ManagedTransactionRequest) -> ManagedTransactionResult:
        fingerprint = _request_fingerprint(request)
        async with self._lock:
            execution = self._executions.get(request.idempotency_key)
            if execution is not None:
                if execution.fingerprint != fingerprint:
                    return self._result(
                        execution, ManagedTransactionStage.CONFLICT, "idempotency_conflict"
                    )
                if execution.terminal is not None:
                    return execution.terminal
                if execution.state.stage is ManagedTransactionStage.RETRY_ACK:
                    return await self._ack(execution)
                if execution.state.stage is ManagedTransactionStage.RETRY_CLEANUP:
                    return await self._retry_cleanup(execution)
                return self._result(execution, execution.state.stage, "duplicate_in_progress")
            execution = _Execution(
                request=request,
                fingerprint=fingerprint,
                state=ManagedTransactionState(
                    transaction_id=request.transaction_id,
                    idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                    request_fingerprint=fingerprint,
                    stage=ManagedTransactionStage.NEW,
                    claim_source=request.claim_source,
                ),
            )
            self._executions[request.idempotency_key] = execution
            try:
                return await self._run(execution)
            except asyncio.CancelledError:
                if execution.terminal is None and execution.state.stage not in {
                    ManagedTransactionStage.RETRY_ACK,
                    ManagedTransactionStage.RETRY_CLEANUP,
                }:
                    result = await asyncio.shield(self._cancel(execution))
                    execution.terminal = result
                raise

    async def retry(self, request: ManagedTransactionRequest) -> ManagedTransactionResult:
        return await self.execute(request)

    async def retry_idempotency(self, idempotency_key: str) -> ManagedTransactionResult:
        async with self._lock:
            execution = self._executions.get(idempotency_key)
            if execution is None:
                raise KeyError("managed transaction is unavailable")
            if execution.terminal is not None:
                return execution.terminal
            if execution.state.stage not in {
                ManagedTransactionStage.RETRY_ACK,
                ManagedTransactionStage.RETRY_CLEANUP,
            }:
                return self._result(
                    execution,
                    execution.state.stage,
                    "retry_not_supported",
                )
            try:
                if execution.state.stage is ManagedTransactionStage.RETRY_CLEANUP:
                    return await self._retry_cleanup(execution)
                return await self._ack(execution)
            except asyncio.CancelledError:
                if execution.terminal is None and execution.state.stage not in {
                    ManagedTransactionStage.RETRY_ACK,
                    ManagedTransactionStage.RETRY_CLEANUP,
                }:
                    result = await asyncio.shield(self._cancel(execution))
                    execution.terminal = result
                raise

    async def resume_pending_ack(
        self, recovery: ManagedPendingAckRecovery
    ) -> ManagedTransactionResult:
        idempotency_key = f"ack-recovery:{recovery.source}:{recovery.delivery_id}"
        async with self._lock:
            existing = self._executions.get(idempotency_key)
            if existing is not None:
                if existing.terminal is not None:
                    return existing.terminal
                if existing.state.stage is ManagedTransactionStage.RETRY_CLEANUP:
                    return await self._retry_cleanup(existing)
                return await self._ack(existing)
            receipt = await self._settings.load_receipt()
            request = ManagedTransactionRequest(
                transaction_id=idempotency_key,
                idempotency_key=idempotency_key,
                correlation_id=recovery.correlation_id,
                claim_source=recovery.source,
                local_secret_key=recovery.ack_secret_key,
                settings_values=_settings_values(receipt),
                expected_settings_revision=receipt.revision,
                reason="managed_delivery_ack_recovery",
            )
            candidate = ManagedCredentialCandidate(
                credential_ref=recovery.credential_ref,
                source=recovery.source,
                secret_value="",
                delivery_ack=ManagedKeyDeliveryAckMetadata(
                    source=recovery.source,
                    delivery_id=recovery.delivery_id,
                    managed_credential_ref=recovery.credential_ref,
                    expires_at=recovery.expires_at,
                    delivery_ack_token="",
                ),
                ack_secret_key=recovery.ack_secret_key,
                post_ack_settings_values=recovery.post_ack_settings_values,
                ack_delivered_settings_values={
                    "state": {"managed_connection": {"pending_delivery_ack_delivered": True}}
                },
            )
            execution = _Execution(
                request=request,
                fingerprint=_request_fingerprint(request),
                state=ManagedTransactionState(
                    transaction_id=idempotency_key,
                    idempotency_key=idempotency_key,
                    correlation_id=recovery.correlation_id,
                    request_fingerprint=_request_fingerprint(request),
                    stage=(
                        ManagedTransactionStage.RETRY_CLEANUP
                        if recovery.delivered or recovery.delivery_confirmed
                        else ManagedTransactionStage.APPLIED
                    ),
                    claim_source=recovery.source,
                    credential_ref=recovery.credential_ref,
                    settings_revision=receipt.revision,
                    ack_delivery_id=recovery.delivery_id,
                ),
                candidate=candidate,
                before_receipt=receipt,
                receipt=receipt,
                runtime_status="applied",
                secret_store=_current(self._secret_store_source),
                secret_commands=_current(self._secrets_source),
                ack_delivered_persisted=recovery.delivered,
                ack_delivery_confirmed=recovery.delivered or recovery.delivery_confirmed,
            )
            self._executions[idempotency_key] = execution
            if recovery.delivered or recovery.delivery_confirmed:
                if not recovery.delivered:
                    delivered = await self._persist_ack_delivered(execution)
                    if delivered is not None:
                        return delivered
                return await self._complete_ack(execution)
            return await self._ack(execution)

    async def _retry_cleanup(self, execution: _Execution) -> ManagedTransactionResult:
        if not execution.ack_delivery_confirmed:
            confirmation = await self._persist_delivery_confirmation(execution)
            if confirmation is not None:
                return confirmation
        if not execution.ack_delivered_persisted:
            delivered = await self._persist_ack_delivered(execution)
            if delivered is not None:
                return delivered
        return await self._complete_ack(execution)

    async def _run(self, execution: _Execution) -> ManagedTransactionResult:
        request = execution.request
        execution.secret_store = _current(self._secret_store_source)
        execution.secret_commands = _current(self._secrets_source)
        try:
            receipt_outcome = await settle_owned(self._settings.load_receipt())
            execution.before_receipt = receipt_outcome.value
            if receipt_outcome.cancellation_count:
                execution.terminal = self._result(
                    execution, ManagedTransactionStage.CANCELLED, "cancelled_before_claim"
                )
                raise asyncio.CancelledError
            if execution.before_receipt.revision != request.expected_settings_revision:
                return self._terminal(
                    execution,
                    ManagedTransactionStage.CONFLICT,
                    "revision_conflict",
                )
            snapshot_outcome = await settle_owned(
                execution.secret_store.snapshot_secret(request.local_secret_key)
            )
            execution.secret_snapshot = snapshot_outcome.value
            if snapshot_outcome.cancellation_count:
                execution.terminal = self._result(
                    execution, ManagedTransactionStage.CANCELLED, "cancelled_before_claim"
                )
                raise asyncio.CancelledError
        except OwnedFailure:
            execution.terminal = self._result(
                execution, ManagedTransactionStage.CANCELLED, "snapshot_failed_after_cancel"
            )
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._terminal(
                execution, ManagedTransactionStage.TERMINAL_FAILURE, "snapshot_failed"
            )
        try:
            claim_outcome = await settle_owned(self._claim.claim(request))
            claim = claim_outcome.value
        except OwnedFailure:
            execution.terminal = self._result(
                execution, ManagedTransactionStage.CANCELLED, "claim_failed_after_cancel"
            )
            raise asyncio.CancelledError
        except Exception:
            return self._terminal(
                execution, ManagedTransactionStage.TERMINAL_FAILURE, "claim_failed"
            )
        finally:
            if request.claim_input is not None and request.claim_input.credential is not None:
                execution.request = replace(
                    request,
                    claim_input=replace(request.claim_input, credential=None),
                )
        execution.state = _state(
            execution.state,
            claim_attempts=1,
            external_claim_ref=claim.external_claim_ref,
        )
        execution.release_outcome = claim.release_outcome
        if claim_outcome.cancellation_count:
            stage = (
                ManagedTransactionStage.COMPENSATION_REQUIRED
                if claim.external_claim_ref
                else ManagedTransactionStage.CANCELLED
            )
            execution.terminal = self._result(execution, stage, "cancelled_during_claim")
            raise asyncio.CancelledError
        if claim.status == "conflict":
            return self._terminal(
                execution, ManagedTransactionStage.CONFLICT, claim.detail_code or "claim_conflict"
            )
        if claim.status != "claimed":
            if claim.failure_settings_values:
                failure_state = await self._commit_claim_failure_state(
                    execution,
                    claim.failure_settings_values,
                    claim.failure_auxiliary_secrets,
                    claim.failure_clear_secret_keys,
                )
                if failure_state is not None:
                    return failure_state
            return self._terminal(
                execution,
                ManagedTransactionStage.TERMINAL_FAILURE,
                claim.detail_code or "claim_failed",
            )
        candidates = tuple(
            candidate for candidate in claim.candidates if candidate.source == request.claim_source
        )
        if len(candidates) != 1:
            stage = (
                ManagedTransactionStage.COMPENSATION_REQUIRED
                if claim.external_claim_ref
                else ManagedTransactionStage.TERMINAL_FAILURE
            )
            return self._terminal(
                execution, stage, "credential_ambiguous" if candidates else "credential_missing"
            )
        candidate = candidates[0]
        execution.candidate = candidate
        execution.state = _state(
            execution.state,
            stage=ManagedTransactionStage.CLAIMED,
            credential_ref=candidate.credential_ref,
            secret_key=request.local_secret_key,
            ack_delivery_id=candidate.delivery_ack.delivery_id if candidate.delivery_ack else None,
            referral_bonus_applied=candidate.referral_bonus_applied,
        )
        try:
            secret_outcome = await settle_owned(
                self._apply_secret_deltas(execution, request, candidate)
            )
            metadata = secret_outcome.value
            if secret_outcome.cancellation_count:
                await self._rollback(execution, "cancelled_during_secret", restore_settings=False)
                raise asyncio.CancelledError
        except OwnedFailure:
            execution.terminal = await self._rollback(
                execution, "secret_failed_after_cancel", restore_settings=False
            )
            raise asyncio.CancelledError
        except Exception:
            return await self._rollback(execution, "secret_failed", restore_settings=False)
        execution.state = _state(
            execution.state,
            stage=ManagedTransactionStage.SECRET_MUTATED,
            secret_revision=metadata.revision,
        )
        try:
            commit_outcome = await settle_owned(
                self._settings.save(
                    SettingsCommitRequest(
                        values=_merged_settings_values(
                            request.settings_values,
                            _candidate_settings_values(candidate),
                        ),
                        expected_revision=request.expected_settings_revision,
                        reason=request.reason,
                        correlation_id=request.correlation_id,
                    )
                )
            )
            commit = commit_outcome.value
        except OwnedFailure:
            execution.terminal = await self._rollback(
                execution, "commit_failed_after_cancel", restore_settings=False
            )
            raise asyncio.CancelledError
        except Exception:
            return await self._rollback(execution, "commit_failed", restore_settings=False)
        if commit_outcome.cancellation_count and (
            not commit.succeeded or commit.receipt is None or commit.snapshot is None
        ):
            detail = (
                "revision_conflict"
                if commit.diagnostics and commit.diagnostics.code == "revision_conflict"
                else "commit_failed_after_cancel"
            )
            execution.terminal = await self._rollback(execution, detail, restore_settings=False)
            raise asyncio.CancelledError
        if not commit.succeeded or commit.receipt is None or commit.snapshot is None:
            detail = (
                "revision_conflict"
                if commit.diagnostics and commit.diagnostics.code == "revision_conflict"
                else "commit_failed"
            )
            return await self._rollback(execution, detail, restore_settings=False)
        execution.receipt = commit.receipt
        execution.attempt_receipts.append(commit.receipt)
        execution.state = _state(
            execution.state,
            stage=ManagedTransactionStage.COMMITTED,
            settings_revision=commit.receipt.revision,
        )
        if commit_outcome.cancellation_count:
            execution.terminal = await self._rollback(
                execution, "cancelled_during_commit", restore_settings=True
            )
            raise asyncio.CancelledError
        try:
            apply_outcome = await settle_owned(
                self._runtime.apply_runtime(
                    RuntimeApplyRequest(
                        commit.receipt, execution.before_receipt, "translation_provider"
                    )
                )
            )
            applied = apply_outcome.value
        except OwnedFailure:
            execution.terminal = await self._rollback(
                execution, "runtime_apply_failed_after_cancel", restore_settings=True
            )
            raise asyncio.CancelledError
        except Exception:
            return await self._rollback(execution, "runtime_apply_failed", restore_settings=True)
        execution.runtime_status = applied.status
        execution.runtime_outcomes.append((commit.receipt.revision, applied.status))
        if apply_outcome.cancellation_count:
            execution.terminal = await self._rollback(
                execution, "cancelled_during_apply", restore_settings=True
            )
            raise asyncio.CancelledError
        if applied.status != "applied":
            return await self._rollback(execution, "runtime_apply_failed", restore_settings=True)
        execution.state = _state(execution.state, stage=ManagedTransactionStage.APPLIED)
        if candidate.delivery_ack is None:
            return self._terminal(execution, ManagedTransactionStage.COMPLETED, None)
        return await self._ack(execution)

    async def _commit_claim_failure_state(
        self,
        execution: _Execution,
        values: Mapping[str, object],
        auxiliary_secrets: tuple[tuple[str, str], ...],
        clear_secret_keys: tuple[str, ...],
    ) -> ManagedTransactionResult | None:
        assert execution.before_receipt is not None
        assert execution.secret_store is not None
        assert execution.secret_commands is not None
        execution.state = _state(
            execution.state,
            stage=ManagedTransactionStage.CLAIMED,
            detail_code="claim_failure_reconciliation",
        )
        cancellation_count = 0
        try:
            for key in clear_secret_keys:
                snapshot_outcome = await settle_owned(execution.secret_store.snapshot_secret(key))
                cancellation_count += snapshot_outcome.cancellation_count
                execution.failure_secret_snapshots.append(snapshot_outcome.value)
                if cancellation_count:
                    execution.terminal = await self._rollback_claim_failure_reconciliation(
                        execution,
                        "cancelled_during_identity_secret_snapshot",
                        cancelled=True,
                        prior_cancellation_count=cancellation_count,
                    )
                    raise asyncio.CancelledError
                clear_outcome = await settle_owned(
                    execution.secret_commands.clear_secret(ClearSecretCommand(key))
                )
                cancellation_count += clear_outcome.cancellation_count
                cleared = clear_outcome.value
                execution.secret_receipts.append((cleared.key, cleared.revision))
                execution.state = _state(
                    execution.state,
                    stage=ManagedTransactionStage.SECRET_MUTATED,
                )
                if cancellation_count:
                    execution.terminal = await self._rollback_claim_failure_reconciliation(
                        execution,
                        "cancelled_during_identity_secret_mutation",
                        cancelled=True,
                        prior_cancellation_count=cancellation_count,
                    )
                    raise asyncio.CancelledError
            for key, value in auxiliary_secrets:
                snapshot_outcome = await settle_owned(execution.secret_store.snapshot_secret(key))
                cancellation_count += snapshot_outcome.cancellation_count
                execution.failure_secret_snapshots.append(snapshot_outcome.value)
                if cancellation_count:
                    execution.terminal = await self._rollback_claim_failure_reconciliation(
                        execution,
                        "cancelled_during_identity_secret_snapshot",
                        cancelled=True,
                        prior_cancellation_count=cancellation_count,
                    )
                    raise asyncio.CancelledError
                store_outcome = await settle_owned(
                    execution.secret_commands.set_secret(SetSecretCommand(key, value))
                )
                cancellation_count += store_outcome.cancellation_count
                stored = store_outcome.value
                execution.secret_receipts.append((stored.key, stored.revision))
                execution.state = _state(
                    execution.state,
                    stage=ManagedTransactionStage.SECRET_MUTATED,
                )
                if cancellation_count:
                    execution.terminal = await self._rollback_claim_failure_reconciliation(
                        execution,
                        "cancelled_during_identity_secret_mutation",
                        cancelled=True,
                        prior_cancellation_count=cancellation_count,
                    )
                    raise asyncio.CancelledError
            if cancellation_count:
                execution.terminal = await self._rollback_claim_failure_reconciliation(
                    execution,
                    "cancelled_during_identity_secret_mutation",
                    cancelled=True,
                    prior_cancellation_count=cancellation_count,
                )
                raise asyncio.CancelledError
            commit_outcome = await settle_owned(
                self._settings.save(
                    SettingsCommitRequest(
                        values=_merged_settings_values(execution.request.settings_values, values),
                        expected_revision=execution.before_receipt.revision,
                        reason="managed_release_claim_reconciliation",
                        correlation_id=execution.request.correlation_id,
                    )
                )
            )
            cancellation_count += commit_outcome.cancellation_count
            commit = commit_outcome.value
        except OwnedFailure as exc:
            execution.terminal = await self._rollback_claim_failure_reconciliation(
                execution,
                "identity_reconciliation_failed_after_cancel",
                cancelled=True,
                prior_cancellation_count=cancellation_count + exc.cancellation_count,
            )
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._rollback_claim_failure_reconciliation(
                execution,
                "claim_failure_reconciliation_failed",
            )
        if not commit.succeeded or commit.receipt is None or commit.snapshot is None:
            result = await self._rollback_claim_failure_reconciliation(
                execution,
                (
                    "revision_conflict"
                    if commit.diagnostics and commit.diagnostics.code == "revision_conflict"
                    else "claim_failure_reconciliation_failed"
                ),
                cancelled=bool(cancellation_count),
                prior_cancellation_count=cancellation_count,
            )
            if cancellation_count:
                execution.terminal = result
                raise asyncio.CancelledError
            return result
        execution.receipt = commit.receipt
        execution.attempt_receipts.append(commit.receipt)
        execution.state = _state(
            execution.state,
            stage=ManagedTransactionStage.COMMITTED,
            settings_revision=commit.receipt.revision,
        )
        if cancellation_count:
            execution.terminal = await self._rollback_claim_failure_reconciliation(
                execution,
                "cancelled_during_identity_commit",
                cancelled=True,
                prior_cancellation_count=cancellation_count,
            )
            raise asyncio.CancelledError
        try:
            apply_outcome = await settle_owned(
                self._runtime.apply_runtime(
                    RuntimeApplyRequest(
                        commit.receipt,
                        execution.before_receipt,
                        "translation_provider",
                    )
                )
            )
            applied = apply_outcome.value
        except OwnedFailure as exc:
            execution.terminal = await self._rollback_claim_failure_reconciliation(
                execution,
                "identity_apply_failed_after_cancel",
                cancelled=True,
                prior_cancellation_count=exc.cancellation_count,
            )
            raise asyncio.CancelledError
        except Exception:
            return await self._rollback_claim_failure_reconciliation(
                execution,
                "claim_failure_reconciliation_apply_failed",
            )
        execution.runtime_status = applied.status
        execution.runtime_outcomes.append((commit.receipt.revision, applied.status))
        if apply_outcome.cancellation_count or applied.status != "applied":
            result = await self._rollback_claim_failure_reconciliation(
                execution,
                (
                    "cancelled_during_identity_apply"
                    if apply_outcome.cancellation_count
                    else "claim_failure_reconciliation_apply_failed"
                ),
                cancelled=bool(apply_outcome.cancellation_count),
                prior_cancellation_count=apply_outcome.cancellation_count,
            )
            if apply_outcome.cancellation_count:
                execution.terminal = result
                raise asyncio.CancelledError
            return result
        execution.state = _state(execution.state, stage=ManagedTransactionStage.APPLIED)
        return None

    async def _rollback_claim_failure_reconciliation(
        self,
        execution: _Execution,
        detail: str,
        *,
        cancelled: bool = False,
        prior_cancellation_count: int = 0,
    ) -> ManagedTransactionResult:
        assert execution.before_receipt is not None
        assert execution.secret_store is not None
        settings_ok = execution.receipt is None
        runtime_ok = execution.receipt is None
        cancellation_count = prior_cancellation_count
        if execution.receipt is not None:
            try:
                restore_commit = await settle_owned(
                    self._settings.save(
                        SettingsCommitRequest(
                            values=_settings_values(execution.before_receipt),
                            expected_revision=execution.receipt.revision,
                            reason="managed_release_claim_reconciliation_rollback",
                            correlation_id=execution.request.correlation_id,
                        )
                    )
                )
                cancellation_count += restore_commit.cancellation_count
                restored = restore_commit.value
                settings_ok = bool(restored.succeeded and restored.receipt is not None)
                if settings_ok:
                    prior = execution.receipt
                    execution.receipt = restored.receipt
                    execution.attempt_receipts.append(restored.receipt)
                    runtime_outcome = await settle_owned(
                        self._runtime.apply_runtime(
                            RuntimeApplyRequest(
                                restored.receipt,
                                prior,
                                "translation_provider",
                            )
                        )
                    )
                    cancellation_count += runtime_outcome.cancellation_count
                    runtime_ok = runtime_outcome.value.status == "applied"
                    execution.runtime_status = runtime_outcome.value.status
                    execution.runtime_outcomes.append(
                        (restored.receipt.revision, runtime_outcome.value.status)
                    )
            except OwnedFailure as exc:
                cancellation_count += exc.cancellation_count
                settings_ok = False
                runtime_ok = False
            except Exception:
                settings_ok = False
                runtime_ok = False
        secrets_ok = True
        for snapshot in reversed(execution.failure_secret_snapshots):
            try:
                restored_secret = await settle_owned(
                    execution.secret_store.restore_secret(snapshot)
                )
                cancellation_count += restored_secret.cancellation_count
                secrets_ok = restored_secret.value.succeeded and secrets_ok
            except OwnedFailure as exc:
                cancellation_count += exc.cancellation_count
                secrets_ok = False
            except Exception:
                secrets_ok = False
        rollback_ok = settings_ok and runtime_ok and secrets_ok
        observed_cancelled = cancelled or cancellation_count > 0
        execution.state = _state(
            execution.state,
            cancellation_count=execution.state.cancellation_count + cancellation_count,
        )
        result = self._terminal(
            execution,
            (
                ManagedTransactionStage.CANCELLED
                if observed_cancelled and rollback_ok
                else ManagedTransactionStage.RECONCILIATION_REQUIRED
            ),
            detail if rollback_ok else f"{detail}_rollback_incomplete",
            rollback_succeeded=rollback_ok,
        )
        if observed_cancelled:
            execution.terminal = result
            raise asyncio.CancelledError
        return result

    async def _apply_secret_deltas(
        self,
        execution: _Execution,
        request: ManagedTransactionRequest,
        candidate: ManagedCredentialCandidate,
    ):
        assert execution.secret_commands is not None
        assert execution.secret_store is not None
        auxiliary_keys = {key for key, _value in candidate.auxiliary_secrets}
        for key in candidate.clear_secret_keys:
            if key == request.local_secret_key or key in auxiliary_keys:
                continue
            execution.auxiliary_secret_snapshots.append(
                await execution.secret_store.snapshot_secret(key)
            )
            cleared = await execution.secret_commands.clear_secret(ClearSecretCommand(key))
            execution.secret_receipts.append((cleared.key, cleared.revision))
        metadata = await execution.secret_commands.set_secret(
            SetSecretCommand(request.local_secret_key, candidate.secret_value)
        )
        execution.secret_receipts.append((metadata.key, metadata.revision))
        if candidate.delivery_ack is not None and candidate.ack_secret_key is not None:
            execution.ack_secret_snapshot = await execution.secret_store.snapshot_secret(
                candidate.ack_secret_key
            )
            confirmation_key = ack_delivered_secret_key(candidate.ack_secret_key)
            execution.ack_confirmation_snapshot = await execution.secret_store.snapshot_secret(
                confirmation_key
            )
            cleared_confirmation = await execution.secret_commands.clear_secret(
                ClearSecretCommand(confirmation_key)
            )
            execution.secret_receipts.append(
                (cleared_confirmation.key, cleared_confirmation.revision)
            )
            ack_metadata = await execution.secret_commands.set_secret(
                SetSecretCommand(
                    candidate.ack_secret_key,
                    candidate.delivery_ack.delivery_ack_token,
                )
            )
            execution.secret_receipts.append((ack_metadata.key, ack_metadata.revision))
        for key, value in candidate.auxiliary_secrets:
            execution.auxiliary_secret_snapshots.append(
                await execution.secret_store.snapshot_secret(key)
            )
            auxiliary_metadata = await execution.secret_commands.set_secret(
                SetSecretCommand(key, value)
            )
            execution.secret_receipts.append((auxiliary_metadata.key, auxiliary_metadata.revision))
        return metadata

    async def _ack(self, execution: _Execution) -> ManagedTransactionResult:
        candidate = execution.candidate
        assert candidate is not None and candidate.delivery_ack is not None
        ack = candidate.delivery_ack
        if _ack_expired(ack.expires_at):
            return self._terminal(
                execution, ManagedTransactionStage.TERMINAL_FAILURE, "ack_expired"
            )
        execution.state = _state(execution.state, ack_attempts=execution.state.ack_attempts + 1)
        try:
            ack_outcome = await settle_owned(
                self._delivery_ack.acknowledge(
                    ManagedAckRequest(
                        execution.request.transaction_id,
                        execution.request.correlation_id,
                        ack.source,
                        ack.delivery_id,
                        candidate.credential_ref,
                        ack.delivery_ack_token,
                    )
                )
            )
            result = ack_outcome.value
        except OwnedFailure:
            execution.terminal = self._result(
                execution,
                ManagedTransactionStage.RECONCILIATION_REQUIRED,
                "ack_failed_after_cancel",
            )
            raise asyncio.CancelledError
        except Exception:
            result = ManagedAckResult(False, "ack_exception")
            ack_outcome = None
        if result.succeeded and result.status in {"acknowledged", "already_acknowledged"}:
            confirmation = await self._persist_delivery_confirmation(execution)
            if confirmation is not None:
                return confirmation
            delivered = await self._persist_ack_delivered(execution)
            if delivered is not None:
                if ack_outcome is not None and ack_outcome.cancellation_count:
                    raise asyncio.CancelledError
                return delivered
            completed = await self._complete_ack(execution)
            if ack_outcome is not None and ack_outcome.cancellation_count:
                execution.terminal = completed
                raise asyncio.CancelledError
            return completed
        if ack_outcome is not None and ack_outcome.cancellation_count:
            retryable = _ack_retryable(result.status)
            cancelled_result = self._result(
                execution,
                (
                    ManagedTransactionStage.RETRY_ACK
                    if retryable
                    else ManagedTransactionStage.TERMINAL_FAILURE
                ),
                result.status,
                retry_supported=retryable,
            )
            if not retryable:
                execution.terminal = cancelled_result
            raise asyncio.CancelledError
        if not _ack_retryable(result.status):
            return self._terminal(
                execution,
                ManagedTransactionStage.TERMINAL_FAILURE,
                result.status,
            )
        return self._result(
            execution,
            ManagedTransactionStage.RETRY_ACK,
            result.status,
            retry_supported=True,
        )

    async def _persist_delivery_confirmation(
        self,
        execution: _Execution,
    ) -> ManagedTransactionResult | None:
        if execution.ack_delivery_confirmed:
            return None
        candidate = execution.candidate
        assert candidate is not None
        if candidate.ack_secret_key is None:
            execution.ack_delivery_confirmed = True
            return None
        assert execution.secret_commands is not None
        try:
            metadata = await execution.secret_commands.set_secret(
                SetSecretCommand(
                    ack_delivered_secret_key(candidate.ack_secret_key),
                    encode_ack_delivery_confirmation(
                        candidate.delivery_ack.source,
                        candidate.delivery_ack.delivery_id,
                        candidate.credential_ref,
                    ),
                )
            )
        except Exception:
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_confirmation_persist_failed",
                retry_supported=True,
            )
        execution.secret_receipts.append((metadata.key, metadata.revision))
        execution.ack_delivery_confirmed = True
        return None

    async def _persist_ack_delivered(
        self, execution: _Execution
    ) -> ManagedTransactionResult | None:
        candidate = execution.candidate
        assert candidate is not None
        delivered_values = candidate.ack_delivered_settings_values
        if not delivered_values and (
            candidate.ack_secret_key is not None or candidate.post_ack_settings_values
        ):
            delivered_values = {
                "state": {"managed_connection": {"pending_delivery_ack_delivered": True}}
            }
        if not delivered_values:
            return None
        assert execution.receipt is not None
        outcome = None
        commit = None
        for _attempt in range(2):
            try:
                outcome = await settle_owned(
                    self._settings.save(
                        SettingsCommitRequest(
                            values=_merged_settings_values(
                                _settings_values(execution.receipt),
                                delivered_values,
                            ),
                            expected_revision=execution.receipt.revision,
                            reason="managed_delivery_ack_delivered",
                            correlation_id=execution.request.correlation_id,
                        )
                    )
                )
                commit = outcome.value
            except OwnedFailure:
                self._result(
                    execution,
                    ManagedTransactionStage.RETRY_CLEANUP,
                    "ack_delivered_commit_failed_after_cancel",
                    retry_supported=True,
                )
                raise asyncio.CancelledError
            except Exception:
                return self._result(
                    execution,
                    ManagedTransactionStage.RETRY_CLEANUP,
                    "ack_delivered_commit_failed",
                    retry_supported=True,
                )
            if commit.succeeded and commit.receipt is not None and commit.snapshot is not None:
                break
            if commit.diagnostics and commit.diagnostics.code == "revision_conflict":
                conflict = await self._rebase_pending_ack(execution, require_delivered=False)
                if conflict is not None:
                    return conflict
                if execution.ack_delivered_persisted:
                    return None
                continue
            result = self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_delivered_commit_failed",
                retry_supported=True,
            )
            if outcome.cancellation_count:
                raise asyncio.CancelledError
            return result
        if commit is None or not commit.succeeded or commit.receipt is None:
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "revision_conflict",
                retry_supported=True,
            )
        execution.ack_delivered_persisted = True
        prior = execution.receipt
        execution.receipt = commit.receipt
        execution.attempt_receipts.append(commit.receipt)
        try:
            apply_outcome = await settle_owned(
                self._runtime.apply_runtime(
                    RuntimeApplyRequest(commit.receipt, prior, "translation_provider")
                )
            )
            applied = apply_outcome.value
        except OwnedFailure:
            self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_delivered_apply_failed_after_cancel",
                retry_supported=True,
            )
            raise asyncio.CancelledError
        except Exception:
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_delivered_apply_failed",
                retry_supported=True,
            )
        execution.runtime_status = applied.status
        execution.runtime_outcomes.append((commit.receipt.revision, applied.status))
        execution.state = _state(
            execution.state,
            settings_revision=commit.receipt.revision,
        )
        if applied.status != "applied":
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_delivered_apply_failed",
                retry_supported=True,
            )
        if outcome.cancellation_count or apply_outcome.cancellation_count:
            execution.state = _state(
                execution.state,
                stage=ManagedTransactionStage.RETRY_CLEANUP,
                detail_code="cancelled_after_ack_delivered",
                retry_supported=True,
            )
            raise asyncio.CancelledError
        return None

    async def _complete_ack(self, execution: _Execution) -> ManagedTransactionResult:
        candidate = execution.candidate
        assert candidate is not None
        if candidate.ack_secret_key is None and not candidate.post_ack_settings_values:
            return self._terminal(execution, ManagedTransactionStage.COMPLETED, None)
        conflict = await self._rebase_pending_ack(execution, require_delivered=True)
        if conflict is not None:
            return conflict
        cancellation_count = 0
        try:
            if candidate.ack_secret_key is not None:
                assert execution.secret_commands is not None
                assert execution.secret_store is not None
                cleanup_secret_snapshot = await execution.secret_store.snapshot_secret(
                    candidate.ack_secret_key
                )
                cleanup_marker_snapshot = await execution.secret_store.snapshot_secret(
                    ack_delivered_secret_key(candidate.ack_secret_key)
                )
                if cleanup_secret_snapshot.existed:
                    assert cleanup_secret_snapshot.revision is not None
                    clear_outcome = await settle_owned(
                        execution.secret_store.compare_and_clear_secret(
                            candidate.ack_secret_key,
                            cleanup_secret_snapshot.revision,
                        )
                    )
                    token_clear = clear_outcome.value
                    cancellation_count += clear_outcome.cancellation_count
                    if token_clear.status == "stale":
                        return self._terminal(
                            execution,
                            ManagedTransactionStage.CONFLICT,
                            "ack_token_stale",
                        )
                    execution.secret_receipts.append((token_clear.key, None))
                if cleanup_marker_snapshot.existed:
                    assert cleanup_marker_snapshot.revision is not None
                    marker_outcome = await settle_owned(
                        execution.secret_store.compare_and_clear_secret(
                            cleanup_marker_snapshot.key,
                            cleanup_marker_snapshot.revision,
                        )
                    )
                    marker_clear = marker_outcome.value
                    cancellation_count += marker_outcome.cancellation_count
                    if marker_clear.status == "stale":
                        return self._terminal(
                            execution,
                            ManagedTransactionStage.CONFLICT,
                            "ack_confirmation_stale",
                        )
                    execution.secret_receipts.append((marker_clear.key, None))
            if candidate.post_ack_settings_values:
                assert execution.receipt is not None
                commit = None
                for _attempt in range(2):
                    commit_outcome = await settle_owned(
                        self._settings.save(
                            SettingsCommitRequest(
                                values=_merged_settings_values(
                                    _settings_values(execution.receipt),
                                    candidate.post_ack_settings_values,
                                ),
                                expected_revision=execution.receipt.revision,
                                reason="managed_delivery_ack_completed",
                                correlation_id=execution.request.correlation_id,
                            )
                        )
                    )
                    commit = commit_outcome.value
                    cancellation_count += commit_outcome.cancellation_count
                    if (
                        commit.succeeded
                        and commit.receipt is not None
                        and commit.snapshot is not None
                    ):
                        break
                    if commit.diagnostics and commit.diagnostics.code == "revision_conflict":
                        conflict = await self._rebase_pending_ack(
                            execution,
                            require_delivered=True,
                        )
                        if conflict is not None:
                            return conflict
                        continue
                    result = self._result(
                        execution,
                        ManagedTransactionStage.RETRY_CLEANUP,
                        "ack_cleanup_commit_failed",
                        retry_supported=True,
                    )
                    if cancellation_count:
                        raise asyncio.CancelledError
                    return result
                if commit is None or not commit.succeeded or commit.receipt is None:
                    return self._result(
                        execution,
                        ManagedTransactionStage.RETRY_CLEANUP,
                        "revision_conflict",
                        retry_supported=True,
                    )
                prior = execution.receipt
                execution.receipt = commit.receipt
                execution.attempt_receipts.append(commit.receipt)
                apply_outcome = await settle_owned(
                    self._runtime.apply_runtime(
                        RuntimeApplyRequest(commit.receipt, prior, "translation_provider")
                    )
                )
                applied = apply_outcome.value
                cancellation_count += apply_outcome.cancellation_count
                execution.runtime_status = applied.status
                execution.runtime_outcomes.append((commit.receipt.revision, applied.status))
                execution.state = _state(
                    execution.state,
                    settings_revision=commit.receipt.revision,
                )
                if applied.status != "applied":
                    result = self._result(
                        execution,
                        ManagedTransactionStage.RETRY_CLEANUP,
                        "ack_cleanup_apply_failed",
                        retry_supported=True,
                    )
                    if cancellation_count:
                        raise asyncio.CancelledError
                    return result
        except OwnedFailure:
            self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_cleanup_failed_after_cancel",
                retry_supported=True,
            )
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_cleanup_failed",
                retry_supported=True,
            )
        result = self._terminal(execution, ManagedTransactionStage.COMPLETED, None)
        if cancellation_count:
            raise asyncio.CancelledError
        return result

    async def _rebase_pending_ack(
        self,
        execution: _Execution,
        *,
        require_delivered: bool,
    ) -> ManagedTransactionResult | None:
        latest = await self._settings.load_receipt()
        candidate = execution.candidate
        assert candidate is not None and candidate.delivery_ack is not None
        managed = latest.envelope.state.managed_connection
        identity = (
            managed.pending_delivery_ack_source,
            managed.pending_delivery_ack_delivery_id,
            managed.pending_delivery_ack_managed_credential_ref,
        )
        expected = (
            candidate.delivery_ack.source,
            candidate.delivery_ack.delivery_id,
            candidate.credential_ref,
        )
        if identity != expected:
            return self._terminal(
                execution,
                ManagedTransactionStage.CONFLICT,
                "pending_ack_identity_conflict",
            )
        execution.receipt = latest
        execution.state = _state(execution.state, settings_revision=latest.revision)
        execution.ack_delivered_persisted = bool(managed.pending_delivery_ack_delivered)
        if require_delivered and not execution.ack_delivered_persisted:
            return self._result(
                execution,
                ManagedTransactionStage.RETRY_CLEANUP,
                "ack_delivered_marker_missing",
                retry_supported=True,
            )
        return None

    async def _cancel(self, execution: _Execution) -> ManagedTransactionResult:
        if execution.state.stage in {ManagedTransactionStage.NEW, ManagedTransactionStage.CLAIMED}:
            return await self._rollback(
                execution, "cancelled", restore_settings=False, cancelled=True
            )
        if execution.state.stage in {
            ManagedTransactionStage.SECRET_MUTATED,
            ManagedTransactionStage.COMMITTED,
        }:
            return await self._rollback(
                execution,
                "cancelled",
                restore_settings=execution.receipt is not None,
                cancelled=True,
            )
        return self._terminal(
            execution, ManagedTransactionStage.RECONCILIATION_REQUIRED, "cancelled_after_apply"
        )

    async def _rollback(
        self,
        execution: _Execution,
        detail: str,
        *,
        restore_settings: bool,
        cancelled: bool = False,
    ) -> ManagedTransactionResult:
        try:
            outcome = await settle_owned(
                self._perform_rollback(
                    execution,
                    detail,
                    restore_settings=restore_settings,
                    cancelled=cancelled,
                )
            )
        except OwnedFailure:
            execution.terminal = self._terminal(
                execution,
                ManagedTransactionStage.RECONCILIATION_REQUIRED,
                "rollback_failed_after_cancel",
                rollback_succeeded=False,
            )
            raise asyncio.CancelledError
        if outcome.cancellation_count:
            execution.terminal = outcome.value
            raise asyncio.CancelledError
        return outcome.value

    async def _perform_rollback(
        self,
        execution: _Execution,
        detail: str,
        *,
        restore_settings: bool,
        cancelled: bool = False,
    ) -> ManagedTransactionResult:
        secret_ok = execution.secret_snapshot is None
        ack_secret_ok = execution.ack_secret_snapshot is None
        auxiliary_secrets_ok = True
        assert execution.secret_store is not None
        for snapshot in reversed(execution.auxiliary_secret_snapshots):
            try:
                restored = await execution.secret_store.restore_secret(snapshot)
                auxiliary_secrets_ok = restored.succeeded and auxiliary_secrets_ok
            except BaseException:
                auxiliary_secrets_ok = False
        if execution.ack_secret_snapshot is not None:
            try:
                ack_secret_ok = (
                    await execution.secret_store.restore_secret(execution.ack_secret_snapshot)
                ).succeeded
            except BaseException:
                ack_secret_ok = False
        if execution.ack_confirmation_snapshot is not None:
            try:
                restored_confirmation = await execution.secret_store.restore_secret(
                    execution.ack_confirmation_snapshot
                )
                ack_secret_ok = restored_confirmation.succeeded and ack_secret_ok
            except BaseException:
                ack_secret_ok = False
        if execution.secret_snapshot is not None:
            try:
                secret_ok = (
                    await execution.secret_store.restore_secret(execution.secret_snapshot)
                ).succeeded
            except BaseException:
                secret_ok = False
        settings_ok = True
        runtime_ok = True
        if (
            restore_settings
            and execution.before_receipt is not None
            and execution.receipt is not None
        ):
            before = execution.before_receipt
            try:
                restored = await self._settings.save(
                    SettingsCommitRequest(
                        values=_settings_values(before),
                        expected_revision=execution.receipt.revision,
                        reason="managed_transaction_rollback",
                        correlation_id=execution.request.correlation_id,
                    )
                )
                settings_ok = bool(restored.succeeded and restored.receipt is not None)
                if settings_ok:
                    prior = execution.receipt
                    execution.attempt_receipts.append(restored.receipt)
                    execution.receipt = restored.receipt
                    execution.state = _state(
                        execution.state,
                        settings_revision=restored.receipt.revision,
                    )
                    runtime = await self._runtime.apply_runtime(
                        RuntimeApplyRequest(restored.receipt, prior, "translation_provider")
                    )
                    runtime_ok = runtime.status == "applied"
                    execution.runtime_status = runtime.status
                    execution.runtime_outcomes.append((restored.receipt.revision, runtime.status))
            except BaseException:
                settings_ok = False
                runtime_ok = False
        rollback_ok = (
            settings_ok and runtime_ok and secret_ok and ack_secret_ok and auxiliary_secrets_ok
        )
        if rollback_ok:
            if execution.state.external_claim_ref:
                stage = ManagedTransactionStage.COMPENSATION_REQUIRED
            elif cancelled:
                stage = ManagedTransactionStage.CANCELLED
            elif detail == "revision_conflict":
                stage = ManagedTransactionStage.CONFLICT
            else:
                stage = ManagedTransactionStage.ROLLED_BACK
        elif execution.state.external_claim_ref:
            stage = ManagedTransactionStage.COMPENSATION_REQUIRED
        else:
            stage = ManagedTransactionStage.RECONCILIATION_REQUIRED
        return self._terminal(execution, stage, detail, rollback_succeeded=rollback_ok)

    def _terminal(
        self,
        execution: _Execution,
        stage: ManagedTransactionStage,
        detail: str | None,
        *,
        rollback_succeeded: bool | None = None,
        retry_supported: bool = False,
    ) -> ManagedTransactionResult:
        result = self._result(
            execution,
            stage,
            detail,
            retry_supported=retry_supported,
            rollback_succeeded=rollback_succeeded,
        )
        execution.terminal = result
        return result

    def _result(
        self,
        execution: _Execution,
        stage: ManagedTransactionStage,
        detail: str | None,
        *,
        retry_supported: bool = False,
        rollback_succeeded: bool | None = None,
    ) -> ManagedTransactionResult:
        execution.state = _state(
            execution.state,
            stage=stage,
            detail_code=detail,
            retry_supported=retry_supported,
        )
        metadata = None
        if execution.state.secret_key is not None:
            metadata = (execution.state.secret_key, execution.state.secret_revision)
        return ManagedTransactionResult(
            execution.state,
            metadata,
            execution.receipt,
            execution.before_receipt,
            execution.runtime_status,
            rollback_succeeded,
            tuple(execution.secret_receipts),
            _diagnostics(stage, detail) if detail is not None else None,
            tuple(execution.attempt_receipts),
            tuple(execution.runtime_outcomes),
            execution.release_outcome,
        )


def _state(state: ManagedTransactionState, **changes: object) -> ManagedTransactionState:
    values = asdict(state)
    values.update(changes)
    return ManagedTransactionState(**values)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(nested) for key, nested in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _request_fingerprint(request: ManagedTransactionRequest) -> str:
    payload = {
        "transaction_id": request.transaction_id,
        "correlation_id": request.correlation_id,
        "claim_source": request.claim_source,
        "local_secret_key": request.local_secret_key,
        "settings_values": _json_value(request.settings_values),
        "expected_settings_revision": request.expected_settings_revision,
        "reason": request.reason,
        "claim_input": (
            None
            if request.claim_input is None
            else {
                "referral_id": request.claim_input.referral_id,
                "identity_present": bool(request.claim_input.identity),
                "credential_present": bool(request.claim_input.credential),
                "asserted_at": request.claim_input.asserted_at,
            }
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


def _merged_settings_values(
    baseline: Mapping[str, object], patch: Mapping[str, object]
) -> Mapping[str, object]:
    merged = {str(key): _mutable(nested) for key, nested in baseline.items()}
    _merge(merged, patch)
    return merged


def _candidate_settings_values(candidate: ManagedCredentialCandidate) -> Mapping[str, object]:
    values = _mutable(candidate.settings_values)
    if not isinstance(values, dict):
        values = {}
    ack = candidate.delivery_ack
    if ack is None:
        return values
    state = values.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        values["state"] = state
    managed = state.setdefault("managed_connection", {})
    if not isinstance(managed, dict):
        managed = {}
        state["managed_connection"] = managed
    managed.update(
        {
            "pending_delivery_ack_source": ack.source,
            "pending_delivery_ack_delivery_id": ack.delivery_id,
            "pending_delivery_ack_managed_credential_ref": candidate.credential_ref,
            "pending_delivery_ack_expires_at": ack.expires_at,
            "pending_delivery_ack_delivered": False,
        }
    )
    return values


def _settings_values(receipt: SettingsCommitReceipt) -> Mapping[str, object]:
    values = _json_value(asdict(receipt.envelope))
    if not isinstance(values, Mapping):
        raise TypeError("settings receipt envelope must serialize to a mapping")
    return values


def _merge(target: dict[str, object], patch: Mapping[str, object]) -> None:
    for key, value in patch.items():
        current = target.get(str(key))
        if isinstance(current, dict) and isinstance(value, Mapping):
            _merge(current, value)
        else:
            target[str(key)] = _mutable(value)


def _mutable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _mutable(nested) for key, nested in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable(item) for item in value]
    return value


def _current(value):  # noqa: ANN001, ANN202
    return value() if callable(value) else value


def _ack_retryable(status: str) -> bool:
    return status in {
        "ack_exception",
        "managed_release_unavailable",
        "network_error",
        "rate_limited",
        "retryable",
        "service_unavailable",
        "temporary",
        "timeout",
        "token_read_failed",
    }


def ack_delivered_secret_key(ack_secret_key: str) -> str:
    return f"{ack_secret_key}.delivered"


def encode_ack_delivery_confirmation(
    source: str,
    delivery_id: str,
    credential_ref: str,
) -> str:
    return json.dumps(
        {
            "credential_ref": credential_ref,
            "delivery_id": delivery_id,
            "source": source,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def ack_delivery_confirmation_matches(
    value: object,
    *,
    source: str,
    delivery_id: str,
    credential_ref: str,
) -> bool:
    if not isinstance(value, str):
        return False
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return False
    return payload == {
        "credential_ref": credential_ref,
        "delivery_id": delivery_id,
        "source": source,
        "version": 1,
    }


def _ack_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _diagnostics(
    stage: ManagedTransactionStage,
    code: str,
) -> ErrorDiagnostics:
    return ErrorDiagnostics(
        component="managed_canonical_transaction",
        operation=stage.value,
        code=code,
        category=DIAGNOSTIC_CATEGORY_TRANSACTION,
        visibility=DIAGNOSTIC_VISIBILITY_BASIC,
        content_policy=CONTENT_POLICY_METADATA_ONLY,
        status_code=None,
        retry_after_ms=None,
        fields={"stage": stage.value},
    )


__all__ = [
    "ManagedAckRequest",
    "ManagedAckResult",
    "ManagedCanonicalTransactionCoordinator",
    "ManagedClaimInput",
    "ManagedClaimPort",
    "ManagedClaimResult",
    "ManagedCredentialCandidate",
    "ManagedDeliveryAckPort",
    "ManagedPendingAckRecovery",
    "ManagedTransactionRequest",
    "ManagedTransactionResult",
    "ManagedTransactionStage",
    "ManagedTransactionState",
    "ack_delivered_secret_key",
    "ack_delivery_confirmation_matches",
    "encode_ack_delivery_confirmation",
]
