from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

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
    managed_secret_key: str | None = field(repr=False)
    remote_key_revision: str | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


QqManagedAssertionFailureSubcode = Literal[
    "invalid_credential",
    "mismatch",
    "lifetime_used",
    "rate_limited",
    "key_unavailable",
    "broker_unavailable",
]


@dataclass(frozen=True, slots=True)
class QqManagedAssertionRequest:
    qq_identity: str = field(repr=False)
    credential: str = field(repr=False)
    asserted_at: str
    metadata: Mapping[str, DiagnosticFieldValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_fields(self.metadata))


@dataclass(frozen=True, slots=True)
class QqManagedEntitlementSnapshot:
    qq_subject_ref: str
    managed_credential_ref: str | None
    expires_at: str | None
    openrouter_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class QqManagedAssertionResult:
    succeeded: bool
    managed_secret_key: str | None = field(repr=False)
    entitlement: QqManagedEntitlementSnapshot | None
    failure_subcode: QqManagedAssertionFailureSubcode | None
    retry_after_ms: int | None
    message: UserMessageRef | None
    diagnostics: ErrorDiagnostics | None


class BrokerClientPort(Protocol):
    async def issue_managed_connection(
        self,
        request: BrokerIssueRequest,
    ) -> BrokerIssueResult: ...

    async def assert_qq_managed_identity(
        self,
        request: QqManagedAssertionRequest,
    ) -> QqManagedAssertionResult: ...


__all__ = [
    "BrokerClientPort",
    "BrokerIssueRequest",
    "BrokerIssueResult",
    "QqManagedAssertionFailureSubcode",
    "QqManagedAssertionRequest",
    "QqManagedAssertionResult",
    "QqManagedEntitlementSnapshot",
]
