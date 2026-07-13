from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports._settings_values import freeze_settings_values
from puripuly_heart.core.messages import RuntimeApplyResult


@dataclass(frozen=True, slots=True)
class RuntimeApplyRequest:
    settings_values: Mapping[str, object]
    reason: str | None
    correlation_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settings_values",
            freeze_settings_values(self.settings_values),
        )


class RuntimeApplyPort(Protocol):
    async def apply_runtime(self, request: RuntimeApplyRequest) -> RuntimeApplyResult: ...


__all__ = [
    "RuntimeApplyPort",
    "RuntimeApplyRequest",
]
