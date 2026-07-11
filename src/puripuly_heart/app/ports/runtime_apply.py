from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.core.messages import RuntimeApplyResult


@dataclass(frozen=True, slots=True)
class RuntimeApplyRequest:
    receipt: SettingsCommitReceipt
    reason: str | None
    correlation_id: str | None


class RuntimeApplyPort(Protocol):
    async def apply_runtime(self, request: RuntimeApplyRequest) -> RuntimeApplyResult: ...


__all__ = [
    "RuntimeApplyPort",
    "RuntimeApplyRequest",
]
