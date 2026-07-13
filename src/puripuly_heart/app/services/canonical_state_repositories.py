from __future__ import annotations

from dataclasses import dataclass

from puripuly_heart.app.ports.canonical_state_repository import (
    CanonicalIntentRepositoryPort,
    CanonicalStateUnitOfWorkPort,
    OperationalStateRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class CanonicalStateRepositories:
    intent: CanonicalIntentRepositoryPort
    operational_state: OperationalStateRepositoryPort
    unit_of_work: CanonicalStateUnitOfWorkPort


__all__ = ["CanonicalStateRepositories"]
