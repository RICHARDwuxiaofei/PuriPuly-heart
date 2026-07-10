from __future__ import annotations

from pathlib import Path

from puripuly_heart.app.ports.provider_verifier import ProviderVerifierPort
from puripuly_heart.app.services.canonical_state_repositories import CanonicalStateRepositories


def create_provider_verifier() -> ProviderVerifierPort:
    from puripuly_heart.app.adapters.provider_verifier import ProviderVerifierAdapter

    return ProviderVerifierAdapter()


def create_canonical_state_repositories(path: Path) -> CanonicalStateRepositories:
    from puripuly_heart.app.adapters.canonical_state_repository import (
        CanonicalIntentRepository,
        CanonicalStateUnitOfWork,
        OperationalStateRepository,
    )

    unit_of_work = CanonicalStateUnitOfWork(path)
    return CanonicalStateRepositories(
        intent=CanonicalIntentRepository(unit_of_work),
        operational_state=OperationalStateRepository(unit_of_work),
        unit_of_work=unit_of_work,
    )
