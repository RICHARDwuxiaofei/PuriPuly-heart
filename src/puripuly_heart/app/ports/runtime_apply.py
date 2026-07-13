from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from puripuly_heart.app.ports.post_commit_runtime import RuntimeOperationalSnapshot
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.core.messages import RuntimeApplyResult


@dataclass(frozen=True, slots=True)
class RuntimeApplyRequest:
    receipt: SettingsCommitReceipt
    before: SettingsCommitReceipt | None = None
    surface: str | None = None
    operational: RuntimeOperationalSnapshot | None = None

    @property
    def revision(self) -> str:
        return self.receipt.revision

    @property
    def reason(self) -> str | None:
        return self.receipt.reason

    @property
    def correlation_id(self) -> str | None:
        return self.receipt.correlation_id


@dataclass(frozen=True, slots=True)
class RuntimeApplyExecution:
    result: RuntimeApplyResult
    completed: tuple[str, ...] = ()
    failed: str | None = None
    skipped: tuple[str, ...] = ()
    reconciliation_required: bool = False
    fields: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "completed", tuple(self.completed))
        object.__setattr__(self, "skipped", tuple(self.skipped))


class RuntimeApplyPort(Protocol):
    async def apply_runtime(self, request: RuntimeApplyRequest) -> RuntimeApplyResult: ...


class RuntimeApplyExecutionPort(Protocol):
    async def apply_runtime_execution(
        self, request: RuntimeApplyRequest
    ) -> RuntimeApplyExecution: ...


__all__ = [
    "RuntimeApplyExecution",
    "RuntimeApplyExecutionPort",
    "RuntimeApplyPort",
    "RuntimeApplyRequest",
]
