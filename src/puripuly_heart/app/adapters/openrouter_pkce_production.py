from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from puripuly_heart.app.adapters.provider_verifier import ProviderVerifierAdapter
from puripuly_heart.app.ports.openrouter_pkce_runtime import OpenRouterPkceRuntimeApplyResult
from puripuly_heart.app.ports.post_commit_runtime import RuntimeOperationalSnapshot
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyRequest
from puripuly_heart.app.ports.secret_store import (
    SecretReadResult,
    SecretSnapshot,
    SecretWriteResult,
)
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitRequest,
    SettingsCommitResult,
    SettingsInitializeRequest,
    SettingsSnapshot,
)
from puripuly_heart.app.services.openrouter_pkce_handoff import OpenRouterPkceHandoffService
from puripuly_heart.app.services.openrouter_pkce_owner import OpenRouterPkceOwner
from puripuly_heart.app.services.secret_settings_transaction import SecretSettingsTransaction
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED,
)
from puripuly_heart.core.openrouter_pkce import OpenRouterPKCEClient


@dataclass(slots=True)
class ProductionSecretStorePort:
    store: object

    async def get_secret(self, key: str) -> SecretReadResult:
        value = await asyncio.to_thread(self.store.get, key)
        return SecretReadResult(key, value, None, None, None)

    async def set_secret(self, key: str, value: str) -> SecretWriteResult:
        await asyncio.to_thread(self.store.set, key, value)
        return SecretWriteResult(True, key, None, None, None)

    async def clear_secret(self, key: str) -> SecretWriteResult:
        await asyncio.to_thread(self.store.delete, key)
        return SecretWriteResult(True, key, None, None, None)

    async def snapshot_secret(self, key: str) -> SecretSnapshot:
        value = await asyncio.to_thread(self.store.get, key)
        return SecretSnapshot(key, value, None, value is not None)

    async def restore_secret(self, snapshot: SecretSnapshot) -> SecretWriteResult:
        if snapshot.existed:
            await asyncio.to_thread(self.store.set, snapshot.key, snapshot.value)
        else:
            await asyncio.to_thread(self.store.delete, snapshot.key)
        return SecretWriteResult(True, snapshot.key, None, None, None)


@dataclass(slots=True)
class ProductionCanonicalSettingsRepository:
    persistence: object
    path: Path
    last_loaded_receipt: object | None = field(init=False, default=None)

    async def load_receipt(self):  # noqa: ANN201
        receipt = await asyncio.to_thread(
            self.persistence.load_receipt,
            self.path,
            reason=None,
            correlation_id=None,
        )
        self.last_loaded_receipt = receipt
        return receipt

    async def load(self) -> SettingsSnapshot:
        receipt = await self.load_receipt()
        return SettingsSnapshot(self.persistence.values_for(receipt.envelope), receipt.revision)

    async def initialize(self, request: SettingsInitializeRequest):  # noqa: ANN201
        return await asyncio.to_thread(
            self.persistence.initialize,
            self.path,
            request.envelope,
            reason=request.reason,
            correlation_id=request.correlation_id,
        )

    async def save(self, request: SettingsCommitRequest) -> SettingsCommitResult:
        before = await self.load_receipt()
        if before.revision != request.expected_revision:
            return SettingsCommitResult(False, None, None, None)
        try:
            next_settings = self.persistence.envelope_from_values(request.values)
            receipt = await asyncio.to_thread(
                self.persistence.persist_delta,
                self.path,
                baseline=before.envelope,
                next_settings=next_settings,
                expected_revision=before.revision,
                reason=request.reason,
                correlation_id=request.correlation_id,
            )
        except Exception:
            return SettingsCommitResult(False, None, None, None)
        return SettingsCommitResult(
            True,
            SettingsSnapshot(self.persistence.values_for(receipt.envelope), receipt.revision),
            None,
            None,
            receipt,
        )


@dataclass(slots=True)
class ApplicationHostPkceRuntimeApply:
    host: object
    repository: ProductionCanonicalSettingsRepository
    operational: RuntimeOperationalSnapshot | None = None

    async def apply_runtime(self, request: RuntimeApplyRequest) -> OpenRouterPkceRuntimeApplyResult:
        before = self.repository.last_loaded_receipt
        if before is None or self.operational is None:
            return OpenRouterPkceRuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        execution = await self.host.apply_committed_runtime(
            before=before,
            after=request.receipt,
            surface="openrouter_pkce",
            cause="pkce",
            operational=self.operational,
        )
        return OpenRouterPkceRuntimeApplyResult(
            (
                RUNTIME_APPLY_STATUS_APPLIED
                if execution.transaction.status
                == TRANSACTION_STATUS_SETTINGS_COMMIT_SUCCESS_RUNTIME_APPLIED
                else RUNTIME_APPLY_STATUS_FAILED
            ),
            execution.transaction.message,
            execution.transaction.diagnostics,
            execution.completed,
            execution.failed,
            execution.reconciliation_required,
        )


def create_production_openrouter_pkce_owner(
    *, host: object, persistence: object, state_path: Path, secrets: object
) -> tuple[OpenRouterPkceOwner, ApplicationHostPkceRuntimeApply]:
    repository = ProductionCanonicalSettingsRepository(persistence, state_path)
    runtime_apply = ApplicationHostPkceRuntimeApply(host, repository)
    handoff = OpenRouterPkceHandoffService(
        ProviderVerifierAdapter(),
        SecretSettingsTransaction(ProductionSecretStorePort(secrets), repository),
        runtime_apply,
    )
    return (
        OpenRouterPkceOwner(
            client_factory=lambda: OpenRouterPKCEClient(callback_origin="http://localhost:3000"),
            handoff=handoff,
        ),
        runtime_apply,
    )


__all__ = ["create_production_openrouter_pkce_owner"]
