from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.post_commit_runtime import RuntimeOperation
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyRequest
from puripuly_heart.core.messages import (
    ErrorDiagnostics,
    RuntimeApplyStatus,
    UserMessageRef,
)


@dataclass(frozen=True, slots=True)
class OpenRouterPkceRuntimeApplyResult:
    status: RuntimeApplyStatus
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None
    completed: tuple[RuntimeOperation, ...] = ()
    failed: RuntimeOperation | None = None
    reconciliation_required: bool = False


class OpenRouterPkceRuntimeApplyPort(Protocol):
    async def apply_runtime(
        self, request: RuntimeApplyRequest
    ) -> OpenRouterPkceRuntimeApplyResult: ...


__all__ = ["OpenRouterPkceRuntimeApplyPort", "OpenRouterPkceRuntimeApplyResult"]
