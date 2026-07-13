from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import ProviderActivationDirective
from puripuly_heart.app.ports.runtime_resources import RuntimeResourceReplacementPlan
from puripuly_heart.app.services.resolved_runtime_adapter import ResolvedRuntimeResourceAdapter
from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_BASIC,
    RUNTIME_APPLY_STATUS_APPLIED,
    RUNTIME_APPLY_STATUS_FAILED,
    ErrorDiagnostics,
    RuntimeApplyResult,
)


@dataclass(slots=True)
class ResolvedProviderActivationAdapter:
    runtime: ResolvedRuntimeResourceAdapter

    async def activate_providers(
        self,
        request: ResolvedRuntimeActivationRequest,
        directive: ProviderActivationDirective,
    ) -> RuntimeApplyResult:
        plan = RuntimeResourceReplacementPlan(
            directive.llm,
            directive.self_stt,
            directive.peer_stt,
        )
        try:
            await self.runtime.replace_runtime_with_plan(request, plan)
        except Exception:
            return RuntimeApplyResult(
                status=RUNTIME_APPLY_STATUS_FAILED,
                message=None,
                diagnostics=ErrorDiagnostics(
                    component="resolved_provider_activation",
                    operation="activate_providers",
                    code="provider_activation_failed",
                    category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
                    visibility=DIAGNOSTIC_VISIBILITY_BASIC,
                    content_policy=CONTENT_POLICY_METADATA_ONLY,
                    status_code=None,
                    retry_after_ms=None,
                    fields={},
                ),
            )
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


__all__ = ["ResolvedProviderActivationAdapter"]
