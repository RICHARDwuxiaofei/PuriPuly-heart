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
class BrokerIssueRequest:
    discord_user_id: str
    local_public_key: str
    local_identity_revision: str | None
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


@dataclass(frozen=True, slots=True)
class BrokerIssueResult:
    succeeded: bool
    broker_connection_id: str | None
    managed_secret_key: str | None
    remote_key_revision: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


class BrokerClientPort(Protocol):
    async def issue_managed_connection(
        self,
        request: BrokerIssueRequest,
    ) -> BrokerIssueResult: ...


__all__ = [
    "BrokerClientPort",
    "BrokerIssueRequest",
    "BrokerIssueResult",
]
