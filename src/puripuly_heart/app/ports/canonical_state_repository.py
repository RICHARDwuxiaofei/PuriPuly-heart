from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.app.ports.application_settings import (
    ApplicationSettingsSnapshot,
    SettingsCommand,
)
from puripuly_heart.app.ports.settings_repository import (
    SettingsCommitReceipt,
    SettingsRevisionConflict,
)
from puripuly_heart.config.settings_vnext.schema import (
    PersistedOperationalState,
    UserIntentSettings,
)
from puripuly_heart.core.messages import ErrorDiagnostics


@dataclass(frozen=True, slots=True)
class CanonicalIntentSnapshot:
    value: UserIntentSettings
    revision: str


@dataclass(frozen=True, slots=True)
class OperationalStateSnapshot:
    value: PersistedOperationalState
    revision: str


@dataclass(frozen=True, slots=True)
class CanonicalEnvelopeSnapshot:
    intent: UserIntentSettings
    operational_state: PersistedOperationalState
    revision: str


class CanonicalRepositoryError(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class CanonicalRepositoryConflict(CanonicalRepositoryError, SettingsRevisionConflict):
    def __init__(self, authoritative: CanonicalEnvelopeSnapshot | str) -> None:
        super().__init__("conflict")
        self.authoritative = (
            copy.deepcopy(authoritative)
            if isinstance(authoritative, CanonicalEnvelopeSnapshot)
            else None
        )


class CanonicalRepositoryCancelled(asyncio.CancelledError):
    def __init__(
        self,
        committed: SettingsCommitReceipt | None = None,
        *,
        cancellation_count: int = 1,
        committed_snapshot: ApplicationSettingsSnapshot | None = None,
        runtime_status: str | None = None,
        diagnostics: ErrorDiagnostics | None = None,
        failure_status: str | None = None,
    ) -> None:
        super().__init__()
        self.committed = committed
        self.cancellation_count = cancellation_count
        self.committed_snapshot = committed_snapshot
        self.runtime_status = runtime_status
        self.diagnostics = diagnostics
        self.failure_status = failure_status


class CanonicalIntentCodecPort(Protocol):
    def snapshot(self, envelope: CanonicalEnvelopeSnapshot) -> ApplicationSettingsSnapshot: ...

    def apply(
        self, envelope: CanonicalEnvelopeSnapshot, command: SettingsCommand
    ) -> tuple[UserIntentSettings, ApplicationSettingsSnapshot]: ...


class CanonicalIntentRepositoryPort(Protocol):
    def load(self) -> CanonicalIntentSnapshot: ...

    def save(
        self,
        value: UserIntentSettings,
        *,
        expected_revision: str,
    ) -> CanonicalIntentSnapshot: ...


class OperationalStateRepositoryPort(Protocol):
    def load(self) -> OperationalStateSnapshot: ...

    def save(
        self,
        value: PersistedOperationalState,
        *,
        expected_revision: str,
    ) -> OperationalStateSnapshot: ...


class CanonicalStateUnitOfWorkPort(Protocol):
    def load(self) -> CanonicalEnvelopeSnapshot: ...

    def set_telemetry_consent(
        self,
        consent: str,
        *,
        expected_revision: str,
    ) -> CanonicalEnvelopeSnapshot: ...

    def mark_telemetry_date_sent(
        self,
        active_date_utc: str,
        expected_anonymous_id: str,
        *,
        expected_revision: str,
    ) -> CanonicalEnvelopeSnapshot: ...


class AsyncCanonicalStateRepositoryPort(Protocol):
    async def load(self) -> CanonicalEnvelopeSnapshot: ...

    async def commit_intent(
        self,
        value: UserIntentSettings,
        *,
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...

    async def commit_operational_state(
        self,
        value: PersistedOperationalState,
        *,
        expected_revision: str,
        reason: str | None,
        correlation_id: str | None,
    ) -> SettingsCommitReceipt: ...


__all__ = [
    "CanonicalIntentRepositoryPort",
    "CanonicalIntentSnapshot",
    "CanonicalEnvelopeSnapshot",
    "CanonicalStateUnitOfWorkPort",
    "OperationalStateRepositoryPort",
    "OperationalStateSnapshot",
    "AsyncCanonicalStateRepositoryPort",
    "CanonicalIntentCodecPort",
    "CanonicalRepositoryCancelled",
    "CanonicalRepositoryConflict",
    "CanonicalRepositoryError",
]
