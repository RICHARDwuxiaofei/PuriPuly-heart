from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from puripuly_heart.app.ports.post_commit_runtime import (
    CommittedRuntimeSynchronizationPort,
    RuntimeMutationSurface,
    SurfaceRuntimeTransactionPort,
)
from puripuly_heart.app.ports.provider_verifier import ProviderVerifierPort
from puripuly_heart.app.ports.runtime_resource_factory import (
    LLMResourceBuilderPort,
    ManagedDelegatePort,
    ManagedReleaseServicePort,
    RuntimeClockPort,
    RuntimeFactoryDiagnosticsPort,
    RuntimeLoggingPort,
    RuntimeSecretReadPort,
    STTResourceBuilderPort,
)
from puripuly_heart.app.ports.runtime_resources import RuntimeHostInstallPort
from puripuly_heart.app.services.canonical_state_repositories import CanonicalStateRepositories
from puripuly_heart.app.services.runtime_activation import (
    CurrentCommittedSettingsPort,
    RuntimeActivationOwner,
)


@dataclass(frozen=True, slots=True)
class AdditiveRuntimeComposition:
    canonical_repositories: CanonicalStateRepositories
    resolver: object
    resource_factory: object
    resolved_adapter: object
    activation_owner: RuntimeActivationOwner
    postcommit_coordinator: object
    surface_transactions: SurfaceRuntimeTransactionPort


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


def create_additive_runtime_composition(
    *,
    state_path: Path,
    host: RuntimeHostInstallPort,
    committed_settings: CurrentCommittedSettingsPort,
    secrets: RuntimeSecretReadPort,
    clock: RuntimeClockPort,
    diagnostics: RuntimeFactoryDiagnosticsPort,
    llm_builder: LLMResourceBuilderPort,
    stt_builder: STTResourceBuilderPort,
    synchronization: CommittedRuntimeSynchronizationPort,
    migrated_surfaces: frozenset[RuntimeMutationSurface] = frozenset(),
    runtime_logging: RuntimeLoggingPort | None = None,
    managed_release_service: ManagedReleaseServicePort | None = None,
    managed_delegate: ManagedDelegatePort | None = None,
) -> AdditiveRuntimeComposition:
    from puripuly_heart.app.adapters.post_commit_provider_activation import (
        ResolvedProviderActivationAdapter,
    )
    from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
        ResolvedRuntimeResourceFactory,
    )
    from puripuly_heart.app.services.canonical_runtime_resolution import (
        CanonicalRuntimeConfigResolver,
    )
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )
    from puripuly_heart.app.services.resolved_runtime_adapter import (
        ResolvedRuntimeResourceAdapter,
    )
    from puripuly_heart.app.services.surface_runtime_transactions import (
        SelectiveSurfaceRuntimeTransactionPort,
    )

    repositories = create_canonical_state_repositories(state_path)
    resolver = CanonicalRuntimeConfigResolver()
    factory = ResolvedRuntimeResourceFactory(
        secrets=secrets,
        clock=clock,
        diagnostics=diagnostics,
        llm_builder=llm_builder,
        stt_builder=stt_builder,
        runtime_logging=runtime_logging,
        managed_release_service=managed_release_service,
        managed_delegate=managed_delegate,
    )
    resolved_adapter = ResolvedRuntimeResourceAdapter(factory=factory, host=host)
    activation_owner = RuntimeActivationOwner(
        resolver=resolver,
        runtime=resolved_adapter,
        committed_settings=committed_settings,
    )
    provider_activation = ResolvedProviderActivationAdapter(resolved_adapter)
    coordinator = PostCommitRuntimeTransactionOwner(provider_activation, synchronization)
    surface_transactions = SelectiveSurfaceRuntimeTransactionPort(
        PostCommitRuntimePlanBuilder(resolver),
        coordinator,
        migrated_surfaces,
    )
    return AdditiveRuntimeComposition(
        repositories,
        resolver,
        factory,
        resolved_adapter,
        activation_owner,
        coordinator,
        surface_transactions,
    )
