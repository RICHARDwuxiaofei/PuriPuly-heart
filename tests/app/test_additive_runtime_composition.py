from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from puripuly_heart.app.adapters.post_commit_provider_activation import (
    ResolvedProviderActivationAdapter,
)
from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
    ResolvedRuntimeResourceFactory,
)
from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
from puripuly_heart.app.ports.post_commit_runtime import (
    ProviderActivationDirective,
    RuntimeMutationProvenance,
    RuntimeOperationalSnapshot,
)
from puripuly_heart.app.ports.runtime_apply import RuntimeApplyRequest
from puripuly_heart.app.ports.runtime_resources import InstalledRuntimeState
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.app.services.post_commit_runtime import PostCommitRuntimeTransactionOwner
from puripuly_heart.app.services.resolved_runtime_adapter import ResolvedRuntimeResourceAdapter
from puripuly_heart.app.services.runtime_activation import RuntimeActivationOwner
from puripuly_heart.app.services.surface_runtime_transactions import (
    SelectiveSurfaceRuntimeTransactionPort,
)
from puripuly_heart.app.wiring_composition import create_additive_runtime_composition
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.messages import RUNTIME_APPLY_STATUS_APPLIED, RuntimeApplyResult
from puripuly_heart.core.orchestrator.hub import ClientHub


class Host:
    def __init__(self) -> None:
        self.install_calls = 0

    async def install_runtime_resources(self, staged):  # noqa: ANN001
        self.install_calls += 1
        raise AssertionError(staged)

    async def current_runtime_state(self) -> InstalledRuntimeState:
        return InstalledRuntimeState({})


class Committed:
    async def load_receipt(self) -> SettingsCommitReceipt:
        return SettingsCommitReceipt(AppSettingsVNext(), "1", "reason", "correlation")


class Secrets:
    def get(self, key: str) -> str | None:
        _ = key
        return None


class Diagnostics:
    def detailed_enabled(self) -> bool:
        return False

    def record_cleanup_failure(self, *, slot: str, exception_class: str) -> None:
        raise AssertionError((slot, exception_class))


class Builder:
    def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError((config, kwargs))


class Resource:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class ResourceBuilder:
    def __init__(self, llm: Resource) -> None:
        self.llm = llm
        self.plans = 0

    def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003
        _ = (config, kwargs)
        self.plans += 1
        return self.llm

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError((config, kwargs))


class Osc:
    def enqueue(self, message) -> None:  # noqa: ANN001
        _ = message

    def send_typing(self, on: bool) -> None:
        _ = on

    def set_typing_reason(self, reason: str, active: bool) -> None:
        _ = (reason, active)

    def send_immediate(self, text: str) -> bool:
        _ = text
        return True

    def process_due(self) -> None:
        return None

    def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError((config, kwargs))


class Synchronization:
    def __init__(self) -> None:
        self.calls = 0

    async def synchronize_runtime(self, request, directive, **context):  # noqa: ANN001, ANN003
        _ = (request, directive, context)
        self.calls += 1
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


def _facts() -> RuntimeOperationalSnapshot:
    return RuntimeOperationalSnapshot(
        translation_enabled=True,
        self_stt_enabled=False,
        self_stt_running=False,
        self_stt_staged=True,
        peer_stt_enabled=False,
        peer_stt_running=False,
        peer_stt_staged=True,
        llm_available=True,
        llm_retry_pending=False,
        self_stt_available=True,
        self_stt_retry_pending=False,
        peer_stt_available=True,
        peer_stt_retry_pending=False,
    )


def test_composition_constructs_complete_explicit_graph_and_binds_host(tmp_path: Path) -> None:
    host, synchronization = Host(), Synchronization()
    composition = create_additive_runtime_composition(
        state_path=tmp_path / "state.json",
        host=host,
        committed_settings=Committed(),
        secrets=Secrets(),
        clock=FakeClock(),
        diagnostics=Diagnostics(),
        llm_builder=Builder(),
        stt_builder=Builder(),
        synchronization=synchronization,
    )

    assert isinstance(composition.resource_factory, ResolvedRuntimeResourceFactory)
    assert isinstance(composition.resolved_adapter, ResolvedRuntimeResourceAdapter)
    assert composition.resolved_adapter.host is host
    assert isinstance(composition.activation_owner, RuntimeActivationOwner)
    assert isinstance(composition.postcommit_coordinator, PostCommitRuntimeTransactionOwner)
    assert isinstance(
        composition.postcommit_coordinator.provider_activation,
        ResolvedProviderActivationAdapter,
    )
    assert isinstance(composition.surface_transactions, SelectiveSurfaceRuntimeTransactionPort)
    assert composition.postcommit_coordinator.synchronization is synchronization


@pytest.mark.asyncio
async def test_default_seam_is_inert_and_does_not_call_host_or_sync(tmp_path: Path) -> None:
    host, synchronization = Host(), Synchronization()
    composition = create_additive_runtime_composition(
        state_path=tmp_path / "state.json",
        host=host,
        committed_settings=Committed(),
        secrets=Secrets(),
        clock=FakeClock(),
        diagnostics=Diagnostics(),
        llm_builder=Builder(),
        stt_builder=Builder(),
        synchronization=synchronization,
    )
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "reason", "correlation")

    with pytest.raises(LookupError, match="has not migrated"):
        await composition.surface_transactions.apply_surface_runtime(
            before=receipt,
            after=receipt,
            provenance=RuntimeMutationProvenance(
                "translation_provider", "settings_surface", "reason", "correlation"
            ),
            operational=_facts(),
        )

    assert host.install_calls == 0
    assert synchronization.calls == 0


def test_ui_modules_only_accept_port_and_do_not_construct_app_adapters_or_repositories() -> None:
    root = Path(__file__).parents[2] / "src" / "puripuly_heart" / "ui"
    source = (root / "controller.py").read_text(encoding="utf-8")
    source += (root / "app.py").read_text(encoding="utf-8")
    assert "app.adapters" not in source
    assert "create_additive_runtime_composition" not in source


@pytest.mark.asyncio
async def test_composed_populated_client_hub_adopts_then_retain_replace_closes_exactly_once(
    tmp_path: Path,
) -> None:
    prior, candidate = Resource(), Resource()
    hub = ClientHub(stt=None, peer_stt=None, llm=prior, osc=Osc())
    builder = ResourceBuilder(candidate)
    synchronization = Synchronization()
    composition = create_additive_runtime_composition(
        state_path=tmp_path / "state.json",
        host=hub,
        committed_settings=Committed(),
        secrets=Secrets(),
        clock=FakeClock(),
        diagnostics=Diagnostics(),
        llm_builder=builder,
        stt_builder=builder,
        synchronization=synchronization,
        migrated_surfaces=frozenset({"translation_provider"}),
    )
    before = SettingsCommitReceipt(AppSettingsVNext(), "1", "reason", "correlation")
    facts = _facts()
    provenance = RuntimeMutationProvenance(
        "translation_provider", "settings_surface", "reason", "correlation"
    )

    retained = await composition.surface_transactions.apply_surface_runtime(
        before=before,
        after=before,
        provenance=provenance,
        operational=facts,
    )
    assert retained.transaction.status == "settings_commit_success_runtime_applied"
    assert hub.llm is prior
    assert prior.close_calls == candidate.close_calls == 0
    assert builder.plans == 0

    changed_settings = replace(
        before.envelope,
        intent=replace(
            before.envelope.intent,
            translation=replace(before.envelope.intent.translation, concurrency_limit=7),
        ),
    )
    after = SettingsCommitReceipt(changed_settings, "2", "reason", "correlation")
    replaced = await composition.surface_transactions.apply_surface_runtime(
        before=before,
        after=after,
        provenance=provenance,
        operational=facts,
    )
    assert replaced.transaction.status == "settings_commit_success_runtime_applied"
    assert hub.llm is candidate
    assert prior.close_calls == 1
    assert candidate.close_calls == 0
    assert builder.plans == 1


@pytest.mark.asyncio
async def test_default_activation_and_explicit_directive_concurrency_cannot_cross_plans() -> None:
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "reason", "correlation")
    config = CanonicalRuntimeConfigResolver().resolve(receipt)

    class RuntimeSpy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def replace_runtime(self, request) -> None:  # noqa: ANN001
            await asyncio.sleep(0)
            self.calls.append(("default", request.revision))

        async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
            await asyncio.sleep(0)
            self.calls.append(("explicit", plan))

    runtime = RuntimeSpy()
    owner = RuntimeActivationOwner(CanonicalRuntimeConfigResolver(), runtime, Committed())
    provider = ResolvedProviderActivationAdapter(runtime)  # type: ignore[arg-type]
    directive = ProviderActivationDirective(
        "clear", "retain", "replace", "staged", "active", "none"
    )
    request = ResolvedRuntimeActivationRequest(config, "explicit", "reason", "correlation")

    default_result, explicit_result = await asyncio.gather(
        owner.apply_runtime(RuntimeApplyRequest(receipt)),
        provider.activate_providers(request, directive),
    )

    assert default_result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert explicit_result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert runtime.calls[0][0] != runtime.calls[1][0]
    explicit_call = next(value for kind, value in runtime.calls if kind == "explicit")
    assert (explicit_call.llm, explicit_call.self_stt, explicit_call.peer_stt) == (
        "clear",
        "retain",
        "replace",
    )
