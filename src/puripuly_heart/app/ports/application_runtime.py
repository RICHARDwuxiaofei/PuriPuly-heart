from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from puripuly_heart.config.resolved import ResolvedApplicationRuntimeConfig

if TYPE_CHECKING:
    from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeActivationRequest:
    config: ResolvedApplicationRuntimeConfig
    revision: str
    reason: str | None
    correlation_id: str | None
    receipt: SettingsCommitReceipt | None = None


class ApplicationRuntimePort(Protocol):
    async def replace_runtime(self, request: ResolvedRuntimeActivationRequest) -> None: ...


__all__ = ["ApplicationRuntimePort", "ResolvedRuntimeActivationRequest"]
