from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from puripuly_heart.core.messages import (
    DiagnosticFieldValue,
    ErrorDiagnostics,
    UserMessageRef,
)


def _freeze_fields(
    values: Mapping[str, DiagnosticFieldValue],
) -> Mapping[str, DiagnosticFieldValue]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class ManagedIdentityPreflightRequest:
    local_secret_key: str
    correlation_id: str | None
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


@dataclass(frozen=True, slots=True)
class ManagedIdentityPreflightResult:
    succeeded: bool
    local_public_key: str | None
    local_identity_revision: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


class ManagedIdentityPort(Protocol):
    async def preflight_managed_identity(
        self,
        request: ManagedIdentityPreflightRequest,
    ) -> ManagedIdentityPreflightResult: ...


__all__ = [
    "ManagedIdentityPort",
    "ManagedIdentityPreflightRequest",
    "ManagedIdentityPreflightResult",
]
