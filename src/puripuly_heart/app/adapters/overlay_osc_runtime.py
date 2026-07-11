from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    DashboardRetryFactsDirective,
    OverlayOscDirective,
    ProviderActivationDirective,
    RuntimeSyncDirective,
)
from puripuly_heart.core.messages import (
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    RuntimeApplyResult,
)


class OverlayOscRuntimeCommandPort(Protocol):
    async def apply_overlay_osc(self, directive: OverlayOscDirective) -> bool: ...

    async def publish_dashboard_retry_facts(
        self, directive: DashboardRetryFactsDirective
    ) -> bool: ...


@dataclass(slots=True)
class RetainedProviderActivation:
    async def activate_providers(
        self,
        request: ResolvedRuntimeActivationRequest,
        directive: ProviderActivationDirective,
    ) -> RuntimeApplyResult:
        _ = request
        if (directive.llm, directive.self_stt, directive.peer_stt) != (
            "retain",
            "retain",
            "retain",
        ):
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


@dataclass(slots=True)
class OverlayOscRuntimeSynchronization:
    runtime: OverlayOscRuntimeCommandPort

    async def synchronize_runtime(
        self,
        request: ResolvedRuntimeActivationRequest,
        directive: RuntimeSyncDirective,
        **context: object,
    ) -> RuntimeApplyResult:
        _ = (request, context)
        if isinstance(directive, OverlayOscDirective):
            applied = await self.runtime.apply_overlay_osc(directive)
        elif isinstance(directive, DashboardRetryFactsDirective):
            applied = await self.runtime.publish_dashboard_retry_facts(directive)
        else:
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        if not applied:
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_FAILED, None, None)
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)
