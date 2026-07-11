from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeActivationRequest:
    config: ResolvedApplicationRuntimeConfig
    revision: str
    reason: str | None
    correlation_id: str | None


class ApplicationRuntimePort(Protocol):
    async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None: ...


__all__ = ["ApplicationRuntimePort", "ResolvedRuntimeActivationRequest"]
