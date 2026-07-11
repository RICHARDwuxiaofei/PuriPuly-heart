from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from puripuly_heart.app.ports.application_runtime import (
    ApplicationRuntimePort,
    ResolvedRuntimeActivationRequest,
)
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyPort, RuntimeApplyRequest
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_BASIC,
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    SEVERITY_WARNING,
    ErrorDiagnostics,
    RuntimeApplyResult,
    UserMessageRef,
)


class RuntimeConfigResolver(Protocol):
    def resolve(self, receipt: SettingsCommitReceipt) -> ResolvedApplicationRuntimeConfig: ...


class CurrentCommittedSettingsPort(Protocol):
    async def load_receipt(self) -> SettingsCommitReceipt: ...


@dataclass(slots=True)
class RuntimeActivationOwner(RuntimeApplyPort):
    resolver: RuntimeConfigResolver
    runtime: ApplicationRuntimePort
    committed_settings: CurrentCommittedSettingsPort
    max_convergence_attempts: int = 4
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _active_revision: str | None = field(default=None, init=False)
    _active_config: ResolvedApplicationRuntimeConfig | None = field(default=None, init=False)
    _active_request: ResolvedRuntimeActivationRequest | None = field(default=None, init=False)

    @property
    def active_revision(self) -> str | None:
        return self._active_revision

    async def apply_runtime(self, request: RuntimeApplyRequest) -> RuntimeApplyResult:
        _ = request
        async with self._lock:
            for _attempt in range(max(1, self.max_convergence_attempts)):
                authoritative = await self.committed_settings.load_receipt()
                if authoritative.revision != self._active_revision:
                    result = await self._replace(authoritative)
                    if result.status != RUNTIME_APPLY_STATUS_APPLIED:
                        return result
                stable = await self.committed_settings.load_receipt()
                if stable.revision == self._active_revision:
                    return _applied_result()
            return _churn_result()

    async def _replace(self, receipt: SettingsCommitReceipt) -> RuntimeApplyResult:
        config = self.resolver.resolve(receipt)
        previous_config = self._active_config
        previous_revision = self._active_revision
        previous_request = self._active_request
        activation_request = ResolvedRuntimeActivationRequest(
            config=config,
            revision=receipt.revision,
            reason=receipt.reason,
            correlation_id=receipt.correlation_id,
        )
        try:
            await self.runtime.replace_runtime(activation_request)
        except Exception:
            compensation_failed = False
            if previous_config is not None:
                try:
                    assert previous_request is not None
                    await self.runtime.replace_runtime(previous_request)
                except Exception:
                    compensation_failed = True
            if compensation_failed:
                self._active_config = None
                self._active_revision = None
                self._active_request = None
            else:
                self._active_config = previous_config
                self._active_revision = previous_revision
                self._active_request = previous_request
            return _failed_result(compensation_failed=compensation_failed)
        self._active_config = config
        self._active_revision = receipt.revision
        self._active_request = activation_request
        return _applied_result()


def _applied_result() -> RuntimeApplyResult:
    return RuntimeApplyResult(status=RUNTIME_APPLY_STATUS_APPLIED, message=None, diagnostics=None)


def _failed_result(*, compensation_failed: bool) -> RuntimeApplyResult:
    return RuntimeApplyResult(
        status=RUNTIME_APPLY_STATUS_FAILED,
        message=UserMessageRef(
            key="settings.mutation.runtime_apply_failed",
            params={"phase": "runtime_apply"},
            severity=SEVERITY_WARNING,
        ),
        diagnostics=ErrorDiagnostics(
            component="runtime_activation",
            operation="replace_runtime",
            code=(
                "runtime_activation_compensation_failed"
                if compensation_failed
                else "runtime_activation_failed"
            ),
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=DIAGNOSTIC_VISIBILITY_BASIC,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields={"compensation_failed": compensation_failed},
        ),
    )


def _churn_result() -> RuntimeApplyResult:
    return RuntimeApplyResult(
        status=RUNTIME_APPLY_STATUS_FAILED,
        message=UserMessageRef(
            key="settings.mutation.runtime_apply_failed",
            params={"phase": "runtime_apply"},
            severity=SEVERITY_WARNING,
        ),
        diagnostics=ErrorDiagnostics(
            component="runtime_activation",
            operation="converge_authoritative_runtime",
            code="runtime_activation_commit_churn",
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=DIAGNOSTIC_VISIBILITY_BASIC,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields={"converged": False},
        ),
    )


__all__ = ["CurrentCommittedSettingsPort", "RuntimeActivationOwner", "RuntimeConfigResolver"]
