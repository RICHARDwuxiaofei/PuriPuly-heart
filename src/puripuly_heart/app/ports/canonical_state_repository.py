from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.config.settings_vnext.schema import (
    PersistedOperationalState,
    UserIntentSettings,
)


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


__all__ = [
    "CanonicalIntentRepositoryPort",
    "CanonicalIntentSnapshot",
    "CanonicalEnvelopeSnapshot",
    "CanonicalStateUnitOfWorkPort",
    "OperationalStateRepositoryPort",
    "OperationalStateSnapshot",
]
