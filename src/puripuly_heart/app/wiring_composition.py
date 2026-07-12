from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


def create_overlay_osc_application_composition(
    *,
    configuration=None,
    hub=None,
    lifecycle_output=None,
    renderer_output=None,
    safe_log=None,
    lifecycle_factories=None,
):  # noqa: ANN001, ANN201
    from puripuly_heart.app.adapters.overlay_lifecycle_production import (
        HubOverlayIngress,
        ProductionOverlayApplication,
        ProductionOverlayLifecycleFactories,
        ResolvedOverlayConfiguration,
    )
    from puripuly_heart.app.adapters.overlay_osc_runtime import (
        OverlayOscRuntimeSynchronization,
        RetainedProviderActivation,
    )
    from puripuly_heart.app.adapters.overlay_ui_projection import ProductionUiProjection
    from puripuly_heart.app.services.canonical_runtime_resolution import (
        CanonicalRuntimeConfigResolver,
    )
    from puripuly_heart.app.services.overlay_osc_application_runtime import (
        OverlayOscApplicationRuntime,
    )
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )
    from puripuly_heart.app.services.surface_runtime_transactions import (
        SelectiveSurfaceRuntimeTransactionPort,
    )

    ui_projection = renderer_output or ProductionUiProjection()
    runtime = OverlayOscApplicationRuntime(
        dashboard=(
            ui_projection if hasattr(ui_projection, "publish_dashboard_runtime_facts") else None
        ),
        configuration=(
            ResolvedOverlayConfiguration(configuration) if configuration is not None else None
        ),
        ingress=HubOverlayIngress(hub) if hub is not None else None,
        factories=lifecycle_factories or ProductionOverlayLifecycleFactories(),
        lifecycle_output=lifecycle_output,
        renderer_output=ui_projection,
        safe_log=safe_log,
    )
    resolver = CanonicalRuntimeConfigResolver()
    owner = PostCommitRuntimeTransactionOwner(
        RetainedProviderActivation(), OverlayOscRuntimeSynchronization(runtime)
    )
    transactions = SelectiveSurfaceRuntimeTransactionPort(
        PostCommitRuntimePlanBuilder(resolver),
        owner,
        frozenset({"overlay_osc_output"}),
    )
    application = ProductionOverlayApplication(runtime)
    if hub is not None:
        application.host = hub
    if configuration is not None:
        application.configuration = runtime.configuration
    if lifecycle_output is None:
        runtime.lifecycle_output = application
    return application, transactions


def create_overlay_osc_surface_runtime(runtime):  # noqa: ANN001, ANN201
    from puripuly_heart.app.adapters.overlay_osc_runtime import (
        OverlayOscRuntimeSynchronization,
        RetainedProviderActivation,
    )
    from puripuly_heart.app.services.canonical_runtime_resolution import (
        CanonicalRuntimeConfigResolver,
    )
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )
    from puripuly_heart.app.services.surface_runtime_transactions import (
        SelectiveSurfaceRuntimeTransactionPort,
    )

    resolver = CanonicalRuntimeConfigResolver()
    owner = PostCommitRuntimeTransactionOwner(
        RetainedProviderActivation(), OverlayOscRuntimeSynchronization(runtime)
    )
    return SelectiveSurfaceRuntimeTransactionPort(
        PostCommitRuntimePlanBuilder(resolver), owner, frozenset({"overlay_osc_output"})
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


@dataclass(frozen=True, slots=True)
class OverlayProductionComposition:
    commands: object
    state: object
    transactions: SurfaceRuntimeTransactionPort
    ui_projection: object
    runtime: object
    logging: object
    vrc: object
    audio_gate: object


@dataclass(frozen=True, slots=True)
class SelfSTTProductionComposition:
    commands: object
    state: object


@dataclass(slots=True)
class _DeferredSelfSTTHost:
    supplier: Callable[[], object | None]

    def lease_stt_provider(self, slot):  # noqa: ANN001, ANN201
        host = self.supplier()
        return None if host is None else host.lease_stt_provider(slot)

    async def clear_self_stt_for_toggle_off(self):  # noqa: ANN201
        host = self.supplier()
        if host is None:
            return None
        return await host.clear_self_stt_for_toggle_off()


@dataclass(frozen=True, slots=True)
class ApplicationRuntimeProductionComposition:
    runtime_host: object
    canonical_commands: object
    secrets: object
    persistence: object
    ui_settings: object

    async def start(self, *, auto_flush_osc: bool = True) -> None:
        await self.ui_settings.start()
        try:
            await self.runtime_host.start(auto_flush_osc=auto_flush_osc)
        except BaseException:
            await self.ui_settings.close()
            raise

    async def close(self) -> None:
        failures: list[BaseException] = []
        try:
            await self.ui_settings.close()
        except BaseException as exc:
            failures.append(exc)
        try:
            await self.runtime_host.shutdown()
        except BaseException as exc:
            failures.append(exc)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("application runtime composition shutdown failed", failures)

    async def shutdown(self) -> None:
        await self.close()


def create_application_runtime_host(
    state_path: Path,
    initial_settings,
    *,
    runtime_logging=None,  # noqa: ANN001
    audio_gate=None,  # noqa: ANN001
    overlay_runtime=None,  # noqa: ANN001
):  # noqa: ANN201
    return create_application_runtime_production_composition(
        state_path,
        initial_settings,
        runtime_logging=runtime_logging,
        audio_gate=audio_gate,
        overlay_runtime=overlay_runtime,
    ).runtime_host


def create_application_runtime_production_composition(
    state_path: Path,
    initial_settings,
    *,
    runtime_logging=None,  # noqa: ANN001
    audio_gate=None,  # noqa: ANN001
    overlay_runtime=None,  # noqa: ANN001
) -> ApplicationRuntimeProductionComposition:
    from puripuly_heart.app.adapters.application_runtime_production import (
        create_production_application_runtime,
    )
    from puripuly_heart.app.services.canonical_command_composition import (
        create_canonical_command_composition,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )
    from puripuly_heart.app.wiring import create_secret_store
    from puripuly_heart.core.clock import SystemClock

    persistence = compose_canonical_settings_persistence()
    legacy = persistence.legacy_projection(initial_settings)
    secrets = create_secret_store(legacy.secrets, config_path=state_path)
    runtime_host = create_production_application_runtime(
        state_path=state_path,
        initial_settings=initial_settings,
        persistence=persistence,
        secrets=secrets,
        clock=SystemClock(),
        runtime_logging=runtime_logging,
        audio_gate=audio_gate,
    )
    if overlay_runtime is not None:
        runtime_host.bind_overlay_runtime(overlay_runtime)
    canonical_commands = create_canonical_command_composition(
        state_path=state_path,
        runtime_host=runtime_host,
        secrets=secrets,
    )
    runtime_host.bind_canonical_commands(canonical_commands)
    from puripuly_heart.app.adapters.audio_device_query import ProductionAudioDeviceQuery
    from puripuly_heart.app.adapters.microphone_test_source import (
        ProductionMicrophoneTestSourceFactory,
    )
    from puripuly_heart.app.adapters.ui_settings_interactions import (
        ProductionUiSettingsInteractions,
    )
    from puripuly_heart.app.services.canonical_secret_commands import UI_SETTINGS_SECRET_KEYS
    from puripuly_heart.app.services.microphone_test import (
        ApplicationMicrophoneTestService,
    )
    from puripuly_heart.app.services.telemetry_operational_state import (
        TelemetryOperationalStateOwner,
    )
    from puripuly_heart.app.services.ui_settings import create_ui_settings_application
    from puripuly_heart.core.runtime.mic_test import MicTestRuntime

    microphone_runtime = MicTestRuntime()
    microphone = ApplicationMicrophoneTestService(
        settings_queries=canonical_commands.settings_queries,
        runtime=microphone_runtime,
        source_factory=ProductionMicrophoneTestSourceFactory(),
    )
    telemetry_owner = TelemetryOperationalStateOwner(
        create_canonical_state_repositories(state_path).unit_of_work
    )
    ui_settings = create_ui_settings_application(
        canonical_commands=canonical_commands,
        interactions=ProductionUiSettingsInteractions(
            canonical_commands,
            provider_verifier=create_provider_verifier(),
            runtime_host=runtime_host,
            telemetry_owner=telemetry_owner,
            overlay=overlay_runtime,
            microphone=microphone,
            persistence=persistence,
            owned_services=(microphone,),
            audio_devices=ProductionAudioDeviceQuery(),
        ),
        secret_keys=UI_SETTINGS_SECRET_KEYS,
    )
    return ApplicationRuntimeProductionComposition(
        runtime_host=runtime_host,
        canonical_commands=canonical_commands,
        secrets=secrets,
        persistence=persistence,
        ui_settings=ui_settings,
    )


def create_self_stt_production_composition(
    *, host_supplier: Callable[[], object | None], audio_lifecycle, ingress
):  # noqa: ANN001, ANN201
    async def unused_audio_loop(**kwargs):  # noqa: ANN003, ANN202
        _ = kwargs

    owner = create_self_stt_channel_composition(
        host=_DeferredSelfSTTHost(host_supplier),
        ingress=ingress,
        source_factory=lambda config: None,
        vad_factory=lambda config: None,
        run_audio_loop=unused_audio_loop,
        audio_lifecycle=audio_lifecycle,
    )
    return SelfSTTProductionComposition(owner, owner)


def create_self_stt_channel_composition(
    *,
    host,
    ingress,
    source_factory,
    vad_factory,
    run_audio_loop,
    audio_lifecycle=None,
):  # noqa: ANN001, ANN201
    from puripuly_heart.core.runtime.self_audio import SelfSTTChannelOwner

    return SelfSTTChannelOwner(
        provider_read_port=host,
        provider_host=host,
        ingress=ingress,
        source_factory=source_factory,
        vad_factory=vad_factory,
        run_audio_loop=run_audio_loop,
        audio_lifecycle=audio_lifecycle,
    )


def create_overlay_production_composition(
    *, configuration=None, hub=None, vrc_microphone=None
):  # noqa: ANN001, ANN201
    from puripuly_heart.app.adapters.overlay_runtime_effects import (
        ProductionOverlaySafeLog,
        ProductionVrcMicrophoneEffects,
    )
    from puripuly_heart.app.adapters.overlay_ui_projection import ProductionUiProjection

    projection = ProductionUiProjection()
    logging = ProductionOverlaySafeLog()
    vrc = vrc_microphone or ProductionVrcMicrophoneEffects()
    commands, transactions = create_overlay_osc_application_composition(
        configuration=configuration,
        hub=hub,
        renderer_output=projection,
        safe_log=logging,
    )
    commands.runtime.dashboard = projection
    commands.runtime.vrc_microphone = vrc
    return OverlayProductionComposition(
        commands, commands, transactions, projection, commands.runtime, logging, vrc, vrc.gate
    )


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
