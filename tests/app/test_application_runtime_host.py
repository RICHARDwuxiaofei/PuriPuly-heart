from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from puripuly_heart.app.ports.post_commit_runtime import RuntimeOperationalSnapshot
from puripuly_heart.app.ports.runtime_resources import RuntimeResourceReplacementPlan
from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
from puripuly_heart.app.services.application_runtime_host import (
    ApplicationRuntimeHost,
    ApplicationRuntimeParts,
)
from puripuly_heart.app.services.canonical_runtime_resolution import CanonicalRuntimeConfigResolver
from puripuly_heart.config.settings import AppSettings
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import RUNTIME_APPLY_STATUS_APPLIED, RuntimeApplyResult


class Hub:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stop_calls = 0
        self.translation_enabled = False
        self.llm_generation = 0
        self.llm_provider = None

    async def start(self, *, auto_flush_osc: bool) -> None:
        self.events.append(f"ingress_start:{auto_flush_osc}")

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("host_close")

    def provider_state_snapshot(self):  # noqa: ANN201
        return SimpleNamespace(
            llm=SimpleNamespace(provider=self.llm_provider, generation=self.llm_generation)
        )

    def clear_context(self) -> None:
        self.events.append("context_clear")


class Peer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append("ingress_freeze")


class SelfOwner(Peer):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.desired = False
        self.signature = None
        self.intent_generation = 0
        self.intent_enabled = False

    def snapshot(self):  # noqa: ANN201
        return SimpleNamespace(
            desired_enabled=self.desired,
            runtime_signature=self.signature,
            intent_generation=self.intent_generation,
            intent_enabled=self.intent_enabled,
        )

    def record_intent(self, enabled: bool) -> int:
        if enabled != self.intent_enabled:
            self.intent_generation += 1
            self.intent_enabled = enabled
        return self.intent_generation

    async def execute(self, command):  # noqa: ANN001, ANN201
        if getattr(command, "record_intent", True):
            self.record_intent(command.enabled)
        self.events.append(f"self:{command.enabled}")
        self.desired = command.enabled
        self.signature = None if command.config is None else command.config.runtime_signature
        return SimpleNamespace()

    async def freeze_for_provider_replacement(self):  # noqa: ANN201
        self.events.append("self:freeze")
        self.desired = False
        return SimpleNamespace()


class Sender:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("sender_close")


class Resolved:
    def __init__(self, events: list[str], unavailable: frozenset[str] = frozenset()) -> None:
        self.events = events
        self.unavailable = unavailable

    async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
        slots = tuple(
            name for name in ("llm", "self_stt", "peer_stt") if getattr(plan, name) == "replace"
        )
        self.events.extend(f"providers_install:{request.revision}:{slot}" for slot in slots)
        if self.unavailable.intersection(slots):
            raise RuntimeError("provider unavailable")


class Receipts:
    def __init__(self, receipt: SettingsCommitReceipt) -> None:
        self.receipt = receipt

    async def load_receipt(self) -> SettingsCommitReceipt:
        return self.receipt


class Transactions:
    def __init__(self) -> None:
        self.calls = []

    async def apply_surface_runtime(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return SimpleNamespace(transaction=SimpleNamespace(status="applied"))


class Composition:
    def __init__(self, events, unavailable):  # noqa: ANN001
        self.events = events
        self.resolved_adapter = Resolved(events, unavailable)
        self.surface_transactions = Transactions()
        self.startup_operational = None

    async def synchronize_startup(self, receipt, operational):  # noqa: ANN001, ANN201
        self.startup_operational = operational
        self.events.append(f"startup_sync:{receipt.revision}")
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    async def resume_peer_stt(self, receipt):  # noqa: ANN001, ANN201
        self.events.append(f"peer:resume:{receipt.revision}")
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    async def synchronize_managed_release_service(self, receipt) -> None:  # noqa: ANN001
        _ = receipt

    async def close(self) -> None:
        return None


def _facts(*, self_enabled: bool = False, peer_enabled: bool = False):
    return RuntimeOperationalSnapshot(
        translation_enabled=True,
        self_stt_enabled=self_enabled,
        self_stt_running=False,
        self_stt_staged=True,
        peer_stt_enabled=peer_enabled,
        peer_stt_running=False,
        peer_stt_staged=True,
        llm_available=True,
        llm_retry_pending=False,
        self_stt_available=True,
        self_stt_retry_pending=False,
        peer_stt_available=True,
        peer_stt_retry_pending=False,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_canonical_resolver_propagates_self_stt_low_latency_to_qwen_llm(
    enabled: bool,
) -> None:
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            stt=replace(settings.intent.stt, low_latency_mode=enabled),
        ),
    )
    receipt = SettingsCommitReceipt(settings, "qwen-policy", "test", None)

    resolved = CanonicalRuntimeConfigResolver().resolve(receipt)

    assert resolved.self_stt.low_latency_enabled is enabled
    assert resolved.llm.qwen_low_latency_mode is enabled


def _host(
    events: list[str],
    receipt: SettingsCommitReceipt,
    *,
    unavailable: frozenset[str] = frozenset(),
    translation_enabled: bool = False,
):
    hub, peer, self_owner, sender = Hub(events), Peer(events), SelfOwner(events), Sender(events)
    composition = Composition(events, unavailable)
    host = ApplicationRuntimeHost(
        parts=ApplicationRuntimeParts(sender, object(), hub, peer, self_owner),
        runtime_composition=composition,
        committed_settings=Receipts(receipt),
        resolver=CanonicalRuntimeConfigResolver(),
        initial_translation_enabled=translation_enabled,
    )
    return host, hub, peer, composition.surface_transactions


@pytest.mark.asyncio
async def test_production_host_starts_translation_disabled_before_starting_ingress() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(
        AppSettingsVNext(), "sha256:authoritative", "startup", "startup-correlation"
    )
    host, _hub, _peer, _transactions = _host(events, receipt)

    await host.start()

    assert events == [
        "providers_install:sha256:authoritative:self_stt",
        "providers_install:sha256:authoritative:peer_stt",
        "startup_sync:sha256:authoritative",
        "ingress_start:True",
    ]
    assert host._runtime_composition.startup_operational.translation_enabled is False
    assert host._runtime_composition.startup_operational.llm_available is False
    assert host._runtime_composition.startup_operational.llm_retry_pending is False
    snapshot = host.translation_snapshot()
    assert snapshot.desired_enabled is False
    assert snapshot.effective_enabled is False
    assert snapshot.provider_available is False


@pytest.mark.asyncio
async def test_production_host_installs_enabled_translation_before_starting_ingress() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "enabled", "startup", None)
    host, _hub, _peer, _transactions = _host(events, receipt, translation_enabled=True)

    await host.start()

    assert events[:3] == [
        "providers_install:enabled:llm",
        "providers_install:enabled:self_stt",
        "providers_install:enabled:peer_stt",
    ]


@pytest.mark.asyncio
async def test_production_host_apply_forwards_authoritative_receipts_and_typed_facts() -> None:
    before = SettingsCommitReceipt(AppSettingsVNext(), "1", "before", "c1")
    after = SettingsCommitReceipt(AppSettingsVNext(), "2", "after", "c2")
    host, _hub, _peer, transactions = _host([], after)
    facts = _facts(self_enabled=True, peer_enabled=True)

    result = await host.apply_committed_runtime(
        before=before,
        after=after,
        surface="stt_language_audio",
        operational=facts,
    )

    assert result.transaction.status == "applied"
    call = transactions.calls[0]
    assert call["before"] is before
    assert call["after"] is after
    assert call["operational"] is facts
    assert call["provenance"].surface == "stt_language_audio"


@pytest.mark.asyncio
async def test_production_host_shutdown_freezes_ingress_then_closes_host_exactly_once() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "shutdown", None)
    host, hub, peer, _transactions = _host(events, receipt)

    await host.shutdown()
    await host.shutdown()

    assert events == ["ingress_freeze", "ingress_freeze", "host_close", "sender_close"]
    assert peer.close_calls == hub.stop_calls == 1


@pytest.mark.asyncio
async def test_unavailable_provider_does_not_block_other_startup_resources_or_ingress() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "actual-revision", "startup", None)
    host, _hub, _peer, _transactions = _host(
        events,
        receipt,
        unavailable=frozenset({"self_stt"}),
        translation_enabled=True,
    )

    await host.start()

    assert events[-1] == "ingress_start:True"
    assert "providers_install:actual-revision:llm" in events
    assert "providers_install:actual-revision:peer_stt" in events


@pytest.mark.asyncio
async def test_real_factory_missing_llm_keeps_independent_stt_slots_available() -> None:
    from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
        ResolvedRuntimeResourceFactory,
    )

    class Resource:
        async def close(self) -> None:
            return None

    class LLMBuilder:
        def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
            _ = (config, kwargs)
            raise RuntimeError("credential unavailable")

    class STTBuilder:
        def __init__(self) -> None:
            self.channels = []

        def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
            _ = kwargs
            self.channels.append(config.channel)
            return Resource()

    class Diagnostics:
        def detailed_enabled(self) -> bool:
            return False

        def record_cleanup_failure(self, **kwargs) -> None:  # noqa: ANN003
            _ = kwargs

    stt = STTBuilder()
    factory = ResolvedRuntimeResourceFactory(
        secrets=object(),
        clock=object(),
        diagnostics=Diagnostics(),
        llm_builder=LLMBuilder(),
        stt_builder=stt,
    )
    installed = []

    class Adapter:
        async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
            staged = await factory.build_resources(request.config, plan)
            installed.extend(staged.candidates)

    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "real-factory", "startup", None)
    host, _hub, _peer, _transactions = _host(events, receipt, translation_enabled=True)
    host._runtime_composition.resolved_adapter = Adapter()

    await host.start()

    assert installed == ["self_stt", "peer_stt"]
    assert stt.channels == ["self", "peer"]
    assert host._runtime_composition.startup_operational.llm_available is False
    assert host._runtime_composition.startup_operational.self_stt_available is True
    assert host._runtime_composition.startup_operational.peer_stt_available is True


@pytest.mark.asyncio
async def test_self_enable_installs_from_current_receipt_before_starting_capture() -> None:
    from puripuly_heart.core.runtime.self_audio import SetSelfSTTEnabled

    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "enable-revision", "toggle", None)
    host, _hub, _peer, _transactions = _host(events, receipt)

    await host.execute(SetSelfSTTEnabled(True))

    assert events == [
        "self:freeze",
        "providers_install:enable-revision:self_stt",
        "self:True",
    ]


@pytest.mark.asyncio
async def test_duplicate_self_enable_with_same_canonical_signature_does_not_replace_slot() -> None:
    from puripuly_heart.core.runtime.self_audio import SetSelfSTTEnabled

    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "enable-revision", "toggle", None)
    host, _hub, _peer, _transactions = _host(events, receipt)

    await host.execute(SetSelfSTTEnabled(True))
    await host.execute(SetSelfSTTEnabled(True))

    assert events.count("providers_install:enable-revision:self_stt") == 1
    assert events.count("self:freeze") == 1


@pytest.mark.asyncio
async def test_translation_commands_prepare_enable_and_clear_through_application_host() -> None:
    from puripuly_heart.app.ports.translation_application import SetTranslationEnabled

    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "translation-revision", "toggle", None)
    host, hub, _peer, _transactions = _host(events, receipt)
    original_replace = host._runtime_composition.resolved_adapter.replace_runtime_with_plan

    async def install(request, plan) -> None:  # noqa: ANN001
        await original_replace(request, plan)
        if plan.llm == "replace":
            hub.llm_provider = object()
            hub.llm_generation += 1
        elif plan.llm == "clear":
            hub.llm_provider = None
            hub.llm_generation += 1

    host._runtime_composition.resolved_adapter.replace_runtime_with_plan = install

    enabled = await host.set_translation_enabled(SetTranslationEnabled(True))
    disabled = await host.set_translation_enabled(SetTranslationEnabled(False))

    assert enabled.status == "applied"
    assert enabled.snapshot.effective_enabled is True
    assert enabled.snapshot.provider_generation == 1
    assert disabled.status == "applied"
    assert disabled.snapshot.effective_enabled is False
    assert disabled.snapshot.provider_available is False
    assert disabled.snapshot.provider_generation == 2


@pytest.mark.asyncio
async def test_translation_enable_unavailable_is_typed_and_keeps_policy_disabled() -> None:
    from puripuly_heart.app.ports.translation_application import SetTranslationEnabled

    receipt = SettingsCommitReceipt(AppSettingsVNext(), "translation-revision", "toggle", None)
    host, hub, _peer, _transactions = _host([], receipt, unavailable=frozenset({"llm"}))

    result = await host.set_translation_enabled(SetTranslationEnabled(True))

    assert result.status == "unavailable"
    assert result.snapshot.desired_enabled is True
    assert result.snapshot.effective_enabled is False
    assert hub.translation_enabled is False


@pytest.mark.asyncio
async def test_set_translation_enabled_warms_supported_provider_and_ignores_warmup_failure() -> (
    None
):
    from puripuly_heart.app.ports.translation_application import SetTranslationEnabled

    class Warmable:
        def __init__(self) -> None:
            self.calls = 0

        async def warmup(self) -> None:
            self.calls += 1
            raise RuntimeError("warmup unavailable")

    receipt = SettingsCommitReceipt(AppSettingsVNext(), "translation-revision", "toggle", None)
    host, hub, _peer, _transactions = _host([], receipt)
    warmable = Warmable()
    hub.llm = SimpleNamespace(inner=warmable)
    original_replace = host._runtime_composition.resolved_adapter.replace_runtime_with_plan

    async def install(request, plan) -> None:  # noqa: ANN001
        await original_replace(request, plan)
        hub.llm_provider = hub.llm
        hub.llm_generation += 1

    host._runtime_composition.resolved_adapter.replace_runtime_with_plan = install

    result = await host.set_translation_enabled(SetTranslationEnabled(True))

    assert result.status == "applied"
    assert result.snapshot.effective_enabled is True
    assert warmable.calls == 1


def test_production_host_has_no_ui_dependency_and_main_owns_construction() -> None:
    root = Path(__file__).parents[2] / "src" / "puripuly_heart"
    host_source = (root / "app" / "services" / "application_runtime_host.py").read_text(
        encoding="utf-8"
    )
    main_source = (root / "main.py").read_text(encoding="utf-8")
    controller_source = (root / "ui" / "controller.py").read_text(encoding="utf-8")
    stt_route = controller_source[
        controller_source.index(
            "async def _apply_stt_language_audio_provider_settings"
        ) : controller_source.index("async def _apply_providers_direct")
    ]

    assert "puripuly_heart.ui" not in host_source
    assert "ClientHub(" not in controller_source
    assert "application_runtime_host = create_application_runtime_host(" in main_source
    assert 'kwargs["application_runtime_host"] = application_runtime_host' in main_source
    assert "audio_gate=composition.audio_gate" in main_source
    assert "_ControllerProviderRuntimeApply" not in stt_route
    assert "_apply_provider_runtime_plan" not in stt_route

    translation_route = controller_source[
        controller_source.index(
            "async def _apply_translation_provider_settings_via_mutation_service"
        ) : controller_source.index(
            "async def _apply_stt_language_audio_provider_settings_via_mutation_service"
        )
    ]
    assert "_ApplicationHostSurfaceRuntimeApply" in translation_route
    assert "_ControllerProviderRuntimeApply" not in translation_route
    assert "create_llm_provider(" not in controller_source
    assert "hub.translation_enabled =" not in controller_source
    assert "translation_enabled=True" not in controller_source
    assert "ManagedOpenRouterReleaseService(" not in controller_source


@pytest.mark.asyncio
async def test_peer_replacement_freezes_ingress_before_atomic_install_and_resumes_after() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ChannelAwareRuntimeResourceHost,
        RuntimePolicyEpoch,
    )

    events: list[str] = []
    resume_token = object()

    class PeerOwner:
        generation = 0

        async def freeze_for_provider_replacement(self):  # noqa: ANN201
            events.append("freeze")
            self.generation += 1
            return resume_token

        def policy_snapshot(self):  # noqa: ANN201
            return SimpleNamespace(generation=self.generation)

        async def resume_after_provider_replacement(self, token) -> None:  # noqa: ANN001
            assert token is resume_token
            events.append("resume")

    class AtomicHost:
        class PeerFinalRuns:
            async def cancel_pending(self) -> None:
                events.append("terminalize")

        peer_final_runs = PeerFinalRuns()

        async def install_runtime_resources(self, staged):  # noqa: ANN001, ANN201
            events.append("install")
            return object()

    wrapper = ChannelAwareRuntimeResourceHost(
        AtomicHost(),
        PeerOwner(),
        SimpleNamespace(freeze_for_provider_replacement=lambda: None),
        RuntimePolicyEpoch(),
    )  # type: ignore[arg-type]
    staged = SimpleNamespace(plan=RuntimeResourceReplacementPlan("retain", "retain", "replace"))

    await wrapper.install_runtime_resources(staged)

    assert events == ["freeze", "terminalize", "install", "resume"]


@pytest.mark.asyncio
async def test_shutdown_retries_only_owner_whose_blocking_close_failed() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "shutdown", None)
    host, hub, peer, _transactions = _host(events, receipt)
    self_owner = host.parts.self_stt
    original_close = self_owner.close
    attempts = 0

    async def flaky_close() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("blocking source close failed")
        await original_close()

    self_owner.close = flaky_close

    with pytest.raises(RuntimeError, match="blocking source close failed"):
        await host.shutdown()
    await host.shutdown()

    assert attempts == 2
    assert peer.close_calls == 1
    assert hub.stop_calls == 1


@pytest.mark.asyncio
async def test_failed_hub_stop_is_retryable_and_host_does_not_close_prematurely() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "shutdown", None)
    host, hub, _peer, _transactions = _host(events, receipt)
    original_stop = hub.stop
    attempts = 0

    async def flaky_stop() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("hub stop failed")
        await original_stop()

    hub.stop = flaky_stop

    with pytest.raises(RuntimeError, match="hub stop failed"):
        await host.shutdown()
    assert host.parts is not None
    await host.shutdown()

    assert attempts == 2
    assert host.parts is None


@pytest.mark.asyncio
async def test_provider_activation_failure_is_typed_and_prevents_synchronization() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        RuntimePolicyEpoch,
        SelectiveProviderActivationAdapter,
    )
    from puripuly_heart.app.ports.post_commit_runtime import RuntimeMutationProvenance
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )

    class FailingRuntime:
        async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
            _ = (request, plan)
            raise RuntimeError("candidate preparation failed")

    class Synchronization:
        calls = 0

        async def synchronize_runtime(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            self.calls += 1
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    receipt = SettingsCommitReceipt(AppSettingsVNext(), "2", "apply", "correlation")
    plan = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver()).build(
        before=None,
        after=receipt,
        provenance=RuntimeMutationProvenance(
            "stt_language_audio", "settings_surface", "apply", "correlation"
        ),
        operational=_facts(self_enabled=True),
    )
    synchronization = Synchronization()
    self_owner = SimpleNamespace(snapshot=lambda: SimpleNamespace(intent_generation=0))
    peer_owner = SimpleNamespace(policy_snapshot=lambda: SimpleNamespace(intent_generation=0))
    owner = PostCommitRuntimeTransactionOwner(
        SelectiveProviderActivationAdapter(
            FailingRuntime(),  # type: ignore[arg-type]
            self_owner,  # type: ignore[arg-type]
            peer_owner,  # type: ignore[arg-type]
            RuntimePolicyEpoch(),
        ),
        synchronization,
    )

    result = await owner.apply(plan)

    assert result.failed == "provider_activation"
    assert result.transaction.status == "settings_commit_success_runtime_degraded"
    assert synchronization.calls == 1
    assert result.transaction.diagnostics.fields["reported_code"] == (
        "provider_set_activation_failed"
    )


@pytest.mark.asyncio
async def test_startup_applies_every_authoritative_directive_before_audio_policy() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionRuntimeComposition,
    )
    from puripuly_heart.app.services.post_commit_runtime import PostCommitRuntimePlanBuilder

    operations: list[str] = []

    class Synchronization:
        async def synchronize_runtime(
            self, request, directive, **kwargs  # noqa: ANN001, ANN003
        ) -> RuntimeApplyResult:
            _ = (request, kwargs)
            operations.append(directive.operation)
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    resolver = CanonicalRuntimeConfigResolver()
    composition = ProductionRuntimeComposition(
        resolved_adapter=SimpleNamespace(),
        surface_transactions=SimpleNamespace(),
        plan_builder=PostCommitRuntimePlanBuilder(resolver),
        synchronization=Synchronization(),  # type: ignore[arg-type]
        resolver=resolver,
    )
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "real-revision", "startup", None)

    result = await composition.synchronize_startup(receipt, _facts())

    assert result.status == RUNTIME_APPLY_STATUS_APPLIED
    assert operations == [
        "translation_policy",
        "language_runtime_clear",
        "overlay_osc",
        "locale_ui_projection",
        "prompt_clipboard",
        "dashboard_retry_facts",
        "audio_vad",
    ]


@pytest.mark.asyncio
async def test_disable_during_atomic_install_wins_before_full_transaction_synchronization() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ChannelAwareRuntimeResourceHost,
        ProductionRuntimeSynchronization,
        RuntimePolicyEpoch,
        SelectiveProviderActivationAdapter,
    )
    from puripuly_heart.app.ports.post_commit_runtime import RuntimeMutationProvenance
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )

    class SelfOwner:
        generation = 1
        desired = True
        intent_generation = 1
        intent_enabled = True
        applied: list[bool] = []

        def snapshot(self):  # noqa: ANN201
            return SimpleNamespace(
                generation=self.generation,
                desired_enabled=self.desired,
                intent_generation=self.intent_generation,
                intent_enabled=self.intent_enabled,
            )

        async def execute(self, command):  # noqa: ANN001, ANN201
            self.applied.append(command.enabled)
            self.desired = command.enabled

        async def freeze_for_provider_replacement(self):  # noqa: ANN201
            self.generation += 1
            self.desired = False

    class PeerOwner:
        generation = 1
        desired = True
        intent_generation = 1
        intent_desired = True
        applied: list[bool] = []

        def policy_snapshot(self):  # noqa: ANN201
            return SimpleNamespace(
                generation=self.generation,
                desired_active=self.desired,
                intent_generation=self.intent_generation,
                intent_desired_active=self.intent_desired,
            )

        async def apply_policy(self, *, config, desired_active, record_intent=True):  # noqa: ANN001
            _ = (config, record_intent)
            self.applied.append(desired_active)
            self.desired = desired_active

        async def freeze_for_provider_replacement(self):  # noqa: ANN201
            token = SimpleNamespace(desired_active=self.desired)
            self.generation += 1
            self.desired = False
            return token

        async def resume_after_provider_replacement(self, token) -> None:  # noqa: ANN001
            self.desired = token.desired_active and self.intent_desired

    self_owner, peer_owner = SelfOwner(), PeerOwner()
    plans = []

    class Hub:
        class PeerFinalRuns:
            async def cancel_pending(self) -> None:
                return None

        peer_final_runs = PeerFinalRuns()

        async def install_runtime_resources(self, staged):  # noqa: ANN001, ANN201
            return staged

        def clear_context(self) -> None:
            return None

    epoch = RuntimePolicyEpoch()
    hub = Hub()
    channel_host = ChannelAwareRuntimeResourceHost(
        hub, peer_owner, self_owner, epoch  # type: ignore[arg-type]
    )

    class Runtime:
        async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
            _ = request
            plans.append(plan)
            self_owner.generation += 1
            self_owner.desired = False
            self_owner.intent_generation += 1
            self_owner.intent_enabled = False
            peer_owner.generation += 1
            peer_owner.desired = False
            peer_owner.intent_generation += 1
            peer_owner.intent_desired = False
            await channel_host.install_runtime_resources(SimpleNamespace(plan=plan))

    activation = SelectiveProviderActivationAdapter(
        Runtime(), self_owner, peer_owner, epoch  # type: ignore[arg-type]
    )
    synchronization = ProductionRuntimeSynchronization(
        hub, self_owner, peer_owner, epoch  # type: ignore[arg-type]
    )
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "3", "race", "c3")
    plan = PostCommitRuntimePlanBuilder(CanonicalRuntimeConfigResolver()).build(
        before=None,
        after=receipt,
        provenance=RuntimeMutationProvenance(
            "stt_language_audio", "settings_surface", "race", "c3"
        ),
        operational=_facts(self_enabled=True, peer_enabled=True),
    )

    result = await PostCommitRuntimeTransactionOwner(activation, synchronization).apply(plan)

    assert result.failed is None
    assert len(plans) == 1
    assert self_owner.desired is False
    assert self_owner.applied == []
    assert peer_owner.applied == [False]


def test_production_microphone_uses_device_then_name_then_default_and_mono_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import puripuly_heart.app.adapters.application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionAudioFactories,
        ProductionAudioRuntimeHooks,
    )
    from puripuly_heart.core.runtime.self_audio import SelfChannelConfig

    backend = (
        CanonicalRuntimeConfigResolver()
        .resolve(SettingsCommitReceipt(AppSettingsVNext(), "1", "audio", None))
        .self_stt
    )
    backend = replace(backend, input_device="Configured Mic")
    opens: list[tuple[object, int]] = []
    resolutions: list[tuple[str, str]] = []

    class Source:
        def __init__(self, *, device, channels, **kwargs):  # noqa: ANN003
            _ = kwargs
            opens.append((device, channels))
            if device in {1, 2} or channels > 1:
                raise RuntimeError("open failed")
            self.device = device
            self.opened_channels = channels
            self.resolved_device_name = "Default Mic"
            self.resolved_device_index = device
            self.actual_sample_rate_hz = 48000

    def resolve(*, host_api: str, device: str) -> int:
        resolutions.append((host_api, device))
        return 1 if host_api else 2

    monkeypatch.setattr(
        production,
        "determine_self_mic_capture_channels",
        lambda **kwargs: SimpleNamespace(preferred_capture_channels=2),
    )
    factories = ProductionAudioFactories(
        detailed_enabled=lambda: False,
        safe_log=lambda message: None,
        hooks=ProductionAudioRuntimeHooks(),
        source_type=Source,
        device_resolver=resolve,
    )

    wrapped = factories.self_source(
        SelfChannelConfig(backend.sample_rate_hz, (backend,), False, backend)
    )

    assert wrapped.source is not None
    assert resolutions[:2] == [
        ("Windows WASAPI", backend.input_device),
        ("", backend.input_device),
    ]
    assert opens[-2:] == [(None, 2), (None, backend.channels)]
    assert wrapped.extra_fields_provider() == {
        "queue_drops": 0,
        "callback_statuses": 0,
        "last_callback_status": None,
        "resolved_device_name": "Default Mic",
        "resolved_device_index": None,
        "resolved_channels": backend.channels,
        "actual_sample_rate_hz": 48000,
        "used_default_fallback": True,
    }


def test_production_managed_release_service_selects_http_and_unavailable_clients() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        create_production_managed_release_service,
    )
    from puripuly_heart.core.managed_openrouter_broker_client import (
        HttpManagedOpenRouterBrokerClient,
    )
    from puripuly_heart.core.managed_openrouter_release import (
        UnavailableManagedOpenRouterReleaseClient,
    )
    from puripuly_heart.core.storage.secrets import InMemorySecretStore

    settings = AppSettings()
    secrets = InMemorySecretStore()
    available = create_production_managed_release_service(settings=settings, secrets=secrets)
    settings.openrouter.broker_base_url = "not-a-url"
    unavailable = create_production_managed_release_service(settings=settings, secrets=secrets)

    assert isinstance(available.client, HttpManagedOpenRouterBrokerClient)
    assert isinstance(unavailable.client, UnavailableManagedOpenRouterReleaseClient)
    assert available.secrets is unavailable.secrets is secrets


@pytest.mark.asyncio
async def test_managed_release_owner_rebuilds_for_broker_change_and_closes_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        def __init__(self, url: str) -> None:
            self.url = url
            self.close_calls = 0
            self.managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            self.close_calls += 1

    services = []

    def create(*, settings, secrets, on_discord_callback_received):  # noqa: ANN001, ANN003
        _ = (secrets, on_discord_callback_received)
        service = Service(settings.openrouter.broker_base_url)
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    persistence = compose_canonical_settings_persistence()
    owner = ProductionManagedReleaseServiceOwner(persistence, tmp_path / "settings.json", object())
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation,
                openrouter_broker_base_url="https://changed.example.test",
            ),
        ),
    )

    await owner.synchronize(SettingsCommitReceipt(first, "1", "first", None))
    await owner.synchronize(SettingsCommitReceipt(second, "2", "second", None))
    await owner.close()
    await owner.close()

    assert [service.url for service in services] == [
        first.intent.translation.openrouter_broker_base_url,
        "https://changed.example.test",
    ]
    assert services[0].close_calls == 1
    assert services[1].close_calls == 1


@pytest.mark.asyncio
async def test_managed_release_owner_persists_managed_operational_state(
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )
    from puripuly_heart.config.settings_vnext.facade import save_vnext_settings
    from puripuly_heart.core.storage.secrets import InMemorySecretStore

    path = tmp_path / "settings.json"
    settings = AppSettingsVNext()
    save_vnext_settings(path, settings)
    persistence = compose_canonical_settings_persistence()
    receipt = persistence.load_receipt(path, reason="test", correlation_id=None)
    owner = ProductionManagedReleaseServiceOwner(persistence, path, InMemorySecretStore())
    await owner.synchronize(receipt)

    owner.service.managed_state.referral_id = "persisted-referral"
    owner.service.managed_state.persist()

    stored = persistence.load_receipt(path, reason="verify", correlation_id=None)
    assert stored.envelope.state.managed_connection.referral_id == "persisted-referral"


@pytest.mark.asyncio
async def test_managed_service_and_provider_replace_as_one_failure_safe_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    events = []

    class Service:
        def __init__(self, url: str) -> None:
            self.url = url
            self.managed_state = SimpleNamespace(_persist=None)
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            events.append(f"service_close:{self.url}")

    services = []

    def create(**kwargs):  # noqa: ANN003
        service = Service(kwargs["settings"].openrouter.broker_base_url)
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation,
                openrouter_broker_base_url="https://second.example.test",
            ),
        ),
    )
    await owner.synchronize(SettingsCommitReceipt(first, "1", "first", None))

    async def install() -> None:
        assert owner.current_service() is services[0]
        assert owner.construction_service() is services[1]
        events.extend(("provider_construct", "provider_install", "provider_displaced_close"))

    await owner.replace_runtime(SettingsCommitReceipt(second, "2", "second", None), install)

    assert owner.current_service() is services[1]
    assert owner.construction_service() is services[1]
    assert events == [
        "provider_construct",
        "provider_install",
        "provider_displaced_close",
        f"service_close:{first.intent.translation.openrouter_broker_base_url}",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("install"), asyncio.CancelledError()])
async def test_managed_service_provider_failure_or_cancellation_retains_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: BaseException,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        def __init__(self) -> None:
            self.managed_state = SimpleNamespace(_persist=None)
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    services = []

    def create(**kwargs):  # noqa: ANN003
        _ = kwargs
        service = Service()
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation,
                openrouter_broker_base_url="https://failed.example.test",
            ),
        ),
    )
    await owner.synchronize(SettingsCommitReceipt(first, "1", "first", None))

    async def fail() -> None:
        raise failure

    with pytest.raises(type(failure)):
        await owner.replace_runtime(SettingsCommitReceipt(second, "2", "second", None), fail)

    assert owner.current_service() is services[0]
    assert services[0].close_calls == 0
    assert services[1].close_calls == 1


@pytest.mark.asyncio
async def test_managed_service_serializes_receipts_and_retries_failed_retirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        def __init__(self, url: str, fail_once: bool = False) -> None:
            self.url = url
            self.fail_once = fail_once
            self.close_calls = 0
            self.managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            self.close_calls += 1
            if self.fail_once and self.close_calls == 1:
                raise RuntimeError("close")

    services = []

    def create(**kwargs):  # noqa: ANN003
        service = Service(kwargs["settings"].openrouter.broker_base_url, not services)
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation, openrouter_broker_base_url="https://second.test"
            ),
        ),
    )
    third = replace(
        second,
        intent=replace(
            second.intent,
            translation=replace(
                second.intent.translation, openrouter_broker_base_url="https://third.test"
            ),
        ),
    )
    await owner.synchronize(SettingsCommitReceipt(first, "1", "first", None))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_install() -> None:
        entered.set()
        await release.wait()

    second_task = asyncio.create_task(
        owner.replace_runtime(SettingsCommitReceipt(second, "2", "second", None), slow_install)
    )
    await entered.wait()
    third_task = asyncio.create_task(
        owner.replace_runtime(
            SettingsCommitReceipt(third, "3", "third", None), lambda: asyncio.sleep(0)
        )
    )
    await asyncio.sleep(0)
    assert owner.current_service() is services[0]
    assert owner.construction_service() is services[1]
    release.set()
    await second_task
    await third_task

    assert owner.current_service() is services[2]
    assert services[0].close_calls == 2
    assert services[1].close_calls == 1


@pytest.mark.asyncio
async def test_managed_discord_callback_is_published_as_typed_application_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ManagedDiscordCallbackEvent,
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    callbacks = []

    class Service:
        managed_state = SimpleNamespace(_persist=None)

        def __init__(self, callback) -> None:  # noqa: ANN001
            self.callback = callback

        async def close(self) -> None:
            return None

    def create(**kwargs):  # noqa: ANN003
        return Service(kwargs["on_discord_callback_received"])

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    owner.callback_output.subscribe(callbacks.append)
    await owner.synchronize(SettingsCommitReceipt(AppSettingsVNext(), "1", "first", None))

    owner.service.callback()

    assert callbacks == [ManagedDiscordCallbackEvent(())]


@pytest.mark.asyncio
async def test_managed_owner_rejects_late_stale_authoritative_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            return None

    created = []

    def create(**kwargs):  # noqa: ANN003
        _ = kwargs
        service = Service()
        created.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    old = AppSettingsVNext()
    newer = replace(
        old,
        intent=replace(
            old.intent,
            translation=replace(
                old.intent.translation, openrouter_broker_base_url="https://newer.test"
            ),
        ),
    )
    old_receipt = SettingsCommitReceipt(old, "old", "old", None)
    new_receipt = SettingsCommitReceipt(newer, "new", "new", None)

    class Authoritative:
        async def load_receipt(self):  # noqa: ANN201
            return new_receipt

    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    await owner.synchronize(new_receipt)
    owner.authoritative_receipts = Authoritative()

    with pytest.raises(RuntimeError, match="stale authoritative runtime receipt"):
        await owner.replace_runtime(old_receipt, lambda: asyncio.sleep(0))

    assert owner.current_service() is created[0]
    assert len(created) == 1


@pytest.mark.asyncio
async def test_managed_shutdown_preserves_repeated_close_failure_for_later_retry(
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls < 3:
                raise RuntimeError("persistent close")

    service = Service()
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    owner.service = service

    with pytest.raises(RuntimeError, match="retirement failed"):
        await owner.close()
    assert owner.closed is False
    with pytest.raises(RuntimeError, match="retirement failed"):
        await owner.close()
    assert owner.closed is False
    await owner.close()

    assert owner.closed is True
    assert service.close_calls == 3
    assert owner._retirement_failures == []


@pytest.mark.asyncio
async def test_managed_shutdown_cancellation_is_re_raised_without_losing_service(
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise asyncio.CancelledError

    service = Service()
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    owner.service = service

    with pytest.raises(asyncio.CancelledError):
        await owner.close()
    assert owner.closed is False
    await owner.close()

    assert owner.closed is True
    assert service.close_calls == 2


@pytest.mark.asyncio
async def test_retry_retirements_cancellation_requeues_current_and_unprocessed_exactly_once(
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )

    class Service:
        def __init__(self, *, cancel_once: bool = False) -> None:
            self.cancel_once = cancel_once
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.cancel_once and self.close_calls == 1:
                raise asyncio.CancelledError

    completed = Service()
    cancelling = Service(cancel_once=True)
    unprocessed = Service()
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    owner._retirement_failures = [completed, cancelling, unprocessed]

    with pytest.raises(asyncio.CancelledError):
        await owner.close()

    assert completed.close_calls == 1
    assert cancelling.close_calls == 1
    assert unprocessed.close_calls == 0
    assert owner._retirement_failures == [cancelling, unprocessed]
    assert owner.closed is False

    await owner.close()

    assert completed.close_calls == 1
    assert cancelling.close_calls == 2
    assert unprocessed.close_calls == 1
    assert owner._retirement_failures == []
    assert owner.closed is True


@pytest.mark.asyncio
async def test_apply_committed_runtime_stages_exact_managed_service_for_coherent_provider_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ManagedAwareResolvedRuntimeAdapter,
        ProductionManagedReleaseServiceOwner,
        ProductionRuntimeComposition,
        RuntimePolicyEpoch,
        SelectiveProviderActivationAdapter,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )
    from puripuly_heart.app.services.post_commit_runtime import (
        PostCommitRuntimePlanBuilder,
        PostCommitRuntimeTransactionOwner,
    )
    from puripuly_heart.app.services.surface_runtime_transactions import (
        SelectiveSurfaceRuntimeTransactionPort,
    )

    class Service:
        def __init__(self, url: str) -> None:
            self.url = url
            self.managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            return None

    services = []

    def create(**kwargs):  # noqa: ANN003
        service = Service(kwargs["settings"].openrouter.broker_base_url)
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    plans = []

    class Runtime:
        async def replace_runtime_with_plan(self, request, plan) -> None:  # noqa: ANN001
            plans.append(plan)
            assert request.receipt is after
            assert owner.construction_service() is services[0]
            assert owner.current_service() is None

    managed_runtime = ManagedAwareResolvedRuntimeAdapter(Runtime(), owner)  # type: ignore[arg-type]
    self_owner = SimpleNamespace(snapshot=lambda: SimpleNamespace(intent_generation=0))
    peer_owner = SimpleNamespace(policy_snapshot=lambda: SimpleNamespace(intent_generation=0))

    class Synchronization:
        async def synchronize_runtime(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            _ = (args, kwargs)
            return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    resolver = CanonicalRuntimeConfigResolver()
    coordinator = PostCommitRuntimeTransactionOwner(
        SelectiveProviderActivationAdapter(
            managed_runtime,  # type: ignore[arg-type]
            self_owner,  # type: ignore[arg-type]
            peer_owner,  # type: ignore[arg-type]
            RuntimePolicyEpoch(),
        ),
        Synchronization(),
    )
    transactions = SelectiveSurfaceRuntimeTransactionPort(
        PostCommitRuntimePlanBuilder(resolver),
        coordinator,
        frozenset({"translation_provider"}),
    )
    composition = ProductionRuntimeComposition(
        managed_runtime,  # type: ignore[arg-type]
        transactions,
        PostCommitRuntimePlanBuilder(resolver),
        Synchronization(),  # type: ignore[arg-type]
        resolver,
        owner,
    )
    settings = AppSettingsVNext()
    settings = replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(
                settings.intent.translation,
                openrouter_broker_base_url="https://receipt.example.test",
            ),
        ),
    )
    after = SettingsCommitReceipt(settings, "receipt", "apply", "correlation")
    host, _hub, _peer, _transactions = _host([], after)
    host._runtime_composition = composition

    result = await host.apply_committed_runtime(
        before=None,
        after=after,
        surface="translation_provider",
        operational=replace(_facts(), llm_available=False, llm_retry_pending=True),
    )

    assert result.transaction.status == "settings_commit_success_runtime_applied"
    assert plans == [RuntimeResourceReplacementPlan("replace", "replace", "replace")]
    assert owner.current_service() is services[0]
    assert services[0].url == "https://receipt.example.test"


@pytest.mark.asyncio
async def test_receipt_becoming_stale_during_install_keeps_current_provider_and_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from puripuly_heart.app.adapters import application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ManagedAwareResolvedRuntimeAdapter,
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.adapters.resolved_runtime_resource_factory import (
        ResolvedRuntimeResourceFactory,
    )
    from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
    from puripuly_heart.app.ports.runtime_resources import (
        RuntimeResourceInstallError,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )
    from puripuly_heart.app.services.resolved_runtime_adapter import (
        ResolvedRuntimeResourceAdapter,
    )

    class Service:
        def __init__(self) -> None:
            self.close_calls = 0
            self.managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            self.close_calls += 1

    services = []

    def create(**kwargs):  # noqa: ANN003
        _ = kwargs
        service = Service()
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation, openrouter_broker_base_url="https://second.test"
            ),
        ),
    )
    third = replace(
        second,
        intent=replace(
            second.intent,
            translation=replace(
                second.intent.translation, openrouter_broker_base_url="https://third.test"
            ),
        ),
    )
    receipts = SimpleNamespace(latest=SettingsCommitReceipt(second, "2", "second", None))

    async def load_receipt():  # noqa: ANN202
        return receipts.latest

    receipts.load_receipt = load_receipt
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    await owner.synchronize(SettingsCommitReceipt(first, "1", "first", None))
    owner.authoritative_receipts = receipts

    class Resource:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    old_provider = Resource()
    candidate_provider = Resource()

    class LLMBuilder:
        def build_llm(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
            _ = (config, kwargs)
            assert owner.construction_service() is services[1]
            return candidate_provider

    class STTBuilder:
        def build_stt(self, config, **kwargs):  # noqa: ANN001, ANN003, ANN201
            raise AssertionError((config, kwargs))

    class Diagnostics:
        def record_cleanup_failure(self, **kwargs) -> None:  # noqa: ANN003
            _ = kwargs

    from puripuly_heart.app.adapters.application_runtime_production import (
        ChannelAwareRuntimeResourceHost,
        RuntimePolicyEpoch,
    )
    from puripuly_heart.core.orchestrator.hub import ClientHub

    class Osc:
        def enqueue(self, message) -> None:  # noqa: ANN001
            _ = message

        def send_typing(self, on) -> None:  # noqa: ANN001
            _ = on

        def set_typing_reason(self, reason, active) -> None:  # noqa: ANN001
            _ = (reason, active)

        def send_immediate(self, text) -> bool:  # noqa: ANN001
            _ = text
            return True

        def process_due(self) -> None:
            return None

    hub = ClientHub(stt=None, peer_stt=None, llm=old_provider, osc=Osc())
    host = ChannelAwareRuntimeResourceHost(
        hub,
        SimpleNamespace(),
        SimpleNamespace(),
        RuntimePolicyEpoch(),
    )

    factory = ResolvedRuntimeResourceFactory(
        object(), object(), Diagnostics(), LLMBuilder(), STTBuilder()
    )
    runtime = ResolvedRuntimeResourceAdapter(factory, host)
    managed = ManagedAwareResolvedRuntimeAdapter(runtime, owner)
    request = ResolvedRuntimeActivationRequest(
        CanonicalRuntimeConfigResolver().resolve(receipts.latest),
        "2",
        "second",
        None,
        receipts.latest,
    )

    await hub._provider_transition_lock.acquire()
    task = asyncio.create_task(
        managed.replace_runtime_with_plan(
            request, RuntimeResourceReplacementPlan("replace", "retain", "retain")
        )
    )
    await asyncio.sleep(0)
    receipts.latest = SettingsCommitReceipt(third, "3", "third", None)
    hub._provider_transition_lock.release()

    with pytest.raises(RuntimeResourceInstallError) as caught:
        await task

    assert caught.value.cause_code == "runtime_install_commit_guard_failed"
    assert owner.current_service() is services[0]
    assert services[0].close_calls == 0
    assert services[1].close_calls == 1
    assert old_provider.close_calls == 0
    assert candidate_provider.close_calls == 1
    installed = await hub.current_runtime_state()
    assert installed.slots["llm"].resource is old_provider


@pytest.mark.asyncio
async def test_self_ingress_forwards_generation_checked_provider_capability() -> None:
    from puripuly_heart.app.adapters.application_runtime_production import HubSelfVadIngress

    calls: list[tuple[object, object]] = []

    class HubIngress:
        async def handle_vad_event(self, event, *, stt_provider=None) -> None:  # noqa: ANN001
            calls.append((event, stt_provider))

    provider_capability = object()
    ingress = HubSelfVadIngress(HubIngress())  # type: ignore[arg-type]

    await ingress.handle_self_vad_event("speech", provider_capability)

    assert calls == [("speech", provider_capability)]


@pytest.mark.asyncio
async def test_production_audio_loop_uses_shared_vrc_gate_and_debug_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import puripuly_heart.app.adapters.application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionAudioFactories,
        ProductionAudioRuntimeHooks,
    )

    captured = {}

    async def fake_loop(**kwargs):  # noqa: ANN003
        captured.update(kwargs)

    monkeypatch.setattr(production, "run_audio_vad_loop", fake_loop)
    gate = object()
    hooks = ProductionAudioRuntimeHooks(capture_fault="capture_attenuate_40db")
    factories = ProductionAudioFactories(
        detailed_enabled=lambda: True,
        safe_log=lambda message: None,
        hooks=hooks,
        audio_gate=gate,
    )

    await factories.self_loop(
        source=object(), vad=object(), sink=object(), target_sample_rate_hz=16000
    )

    assert captured["audio_gate"] is gate
    assert hooks.capture_fault_profile() == "capture_attenuate_40db"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_committed_provider_settlement_retains_matching_managed_services_until_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cancel_close: bool,
) -> None:
    import puripuly_heart.app.adapters.application_runtime_production as production
    from puripuly_heart.app.adapters.application_runtime_production import (
        ManagedAwareResolvedRuntimeAdapter,
        ProductionManagedReleaseServiceOwner,
    )
    from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
    from puripuly_heart.app.ports.runtime_resources import (
        InstalledRuntimeState,
        ResourceRef,
        RuntimeCommittedSettlementFailure,
        RuntimeInstallSuccess,
        RuntimeResourceReplacementPlan,
        StagedRuntimeResources,
    )
    from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
    from puripuly_heart.app.services.canonical_runtime_resolution import (
        CanonicalRuntimeConfigResolver,
    )
    from puripuly_heart.app.services.canonical_settings_persistence import (
        compose_canonical_settings_persistence,
    )
    from puripuly_heart.app.services.resolved_runtime_adapter import (
        ResolvedRuntimeResourceAdapter,
    )
    from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext

    class Service:
        def __init__(self) -> None:
            self.close_calls = 0
            self.managed_state = SimpleNamespace(_persist=None)

        async def close(self) -> None:
            self.close_calls += 1

    services: list[Service] = []

    def create(**kwargs):  # noqa: ANN003, ANN202
        _ = kwargs
        service = Service()
        services.append(service)
        return service

    monkeypatch.setattr(production, "create_production_managed_release_service", create)
    first = AppSettingsVNext()
    second = replace(
        first,
        intent=replace(
            first.intent,
            translation=replace(
                first.intent.translation, openrouter_broker_base_url="https://next.test"
            ),
        ),
    )
    receipt1 = SettingsCommitReceipt(first, "1", "first", None)
    receipt2 = SettingsCommitReceipt(second, "2", "second", None)
    owner = ProductionManagedReleaseServiceOwner(
        compose_canonical_settings_persistence(), tmp_path / "settings.json", object()
    )
    await owner.synchronize(receipt1)

    class Provider:
        def __init__(self, fail_twice: bool = False) -> None:
            self.close_calls = 0
            self.fail_twice = fail_twice

        async def close(self) -> None:
            self.close_calls += 1
            if self.fail_twice and self.close_calls <= 2:
                if cancel_close:
                    raise asyncio.CancelledError
                raise RuntimeError("provider close failed")

    prior_provider = Provider(fail_twice=True)
    candidate_provider = Provider()
    prior_ref = ResourceRef("prior", prior_provider)
    candidate_ref = ResourceRef("candidate", candidate_provider)
    initial = InstalledRuntimeState({"llm": prior_ref})
    committed = InstalledRuntimeState({"llm": candidate_ref})

    class Factory:
        async def build_resources(self, config, plan):  # noqa: ANN001, ANN201
            _ = config
            return StagedRuntimeResources(plan, {"llm": candidate_ref})

    class Host:
        async def current_runtime_state(self):  # noqa: ANN201
            return initial

        async def install_runtime_resources(self, staged):  # noqa: ANN001, ANN201
            _ = staged
            return RuntimeInstallSuccess(
                committed, frozenset({"candidate"}), displaced=(prior_ref,)
            )

    runtime = ResolvedRuntimeResourceAdapter(Factory(), Host())
    runtime._active_state = initial
    runtime._ownership_state_known = True
    managed = ManagedAwareResolvedRuntimeAdapter(runtime, owner)
    request = ResolvedRuntimeActivationRequest(
        CanonicalRuntimeConfigResolver().resolve(receipt2),
        "2",
        "second",
        None,
        receipt2,
    )
    error = asyncio.CancelledError if cancel_close else RuntimeCommittedSettlementFailure

    with pytest.raises(error):
        await managed.replace_runtime_with_plan(
            request, RuntimeResourceReplacementPlan("replace", "retain", "retain")
        )

    assert owner.current_service() is services[1]
    assert candidate_provider.close_calls == 0
    assert prior_provider.close_calls == 1
    assert services[0].close_calls == 0
    assert services[1].close_calls == 0

    with pytest.raises(asyncio.CancelledError if cancel_close else RuntimeError):
        await owner.close()

    assert prior_provider.close_calls == 2
    assert services[0].close_calls == 0
    assert services[1].close_calls == 0
    assert owner.closed is False

    await owner.close()

    assert prior_provider.close_calls == 3
    assert services[0].close_calls == 1
    assert services[1].close_calls == 1
    assert owner.closed is True


def test_resolved_stt_builder_wires_final_suppression_and_stt_fault_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import puripuly_heart.app.wiring as wiring
    from puripuly_heart.app.adapters.application_runtime_production import (
        ProductionAudioRuntimeHooks,
        ResolvedSTTBuilderAdapter,
    )
    from puripuly_heart.core.clock import FakeClock

    backend = (
        CanonicalRuntimeConfigResolver()
        .resolve(SettingsCommitReceipt(AppSettingsVNext(), "1", "audio", None))
        .self_stt
    )
    notifications: list[object] = []
    hooks = ProductionAudioRuntimeHooks(
        stt_fault="stt_input_low_snr_vad_pass",
        final_suppressed_callback=notifications.append,
    )
    monkeypatch.setattr(wiring, "create_stt_backend_from_resolved_config", lambda *a, **k: object())

    provider = ResolvedSTTBuilderAdapter(hooks).build_stt(
        backend,
        secrets=object(),
        diagnostics=SimpleNamespace(detailed_enabled=lambda: False),
        clock=FakeClock(),
        runtime_logging=None,
    )

    assert provider.stt_input_fault_profile_provider() == "stt_input_low_snr_vad_pass"
    provider.on_final_transcript_suppressed("notice")
    assert notifications == ["notice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["self_stt", "peer_stt"])
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_non_llm_committed_settlement_is_owned_until_general_shutdown_retry(
    slot: str,
    cancel_close: bool,
) -> None:
    from puripuly_heart.app.adapters.application_runtime_production import (
        ManagedAwareResolvedRuntimeAdapter,
    )
    from puripuly_heart.app.ports.application_runtime import ResolvedRuntimeActivationRequest
    from puripuly_heart.app.ports.runtime_resources import (
        ResourceRef,
        RuntimeCommittedSettlementFailure,
        RuntimeResourceReplacementPlan,
        StagedRuntimeResources,
    )
    from puripuly_heart.app.ports.settings_repository import SettingsCommitReceipt
    from puripuly_heart.app.services.canonical_runtime_resolution import (
        CanonicalRuntimeConfigResolver,
    )
    from puripuly_heart.app.services.resolved_runtime_adapter import (
        ResolvedRuntimeResourceAdapter,
    )
    from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
    from puripuly_heart.core.orchestrator.hub import ClientHub

    class Provider:
        def __init__(self, fail_twice: bool = False) -> None:
            self.close_calls = 0
            self.fail_twice = fail_twice

        async def close(self) -> None:
            self.close_calls += 1
            if self.fail_twice and self.close_calls <= 2:
                if cancel_close:
                    raise asyncio.CancelledError
                raise RuntimeError("provider close failed")

    class Osc:
        def enqueue(self, message) -> None:  # noqa: ANN001
            _ = message

        def send_typing(self, on) -> None:  # noqa: ANN001
            _ = on

        def set_typing_reason(self, reason, active) -> None:  # noqa: ANN001
            _ = (reason, active)

        def send_immediate(self, text) -> bool:  # noqa: ANN001
            _ = text
            return True

        def process_due(self) -> None:
            return None

    prior = Provider(fail_twice=True)
    candidate = Provider()
    hub = ClientHub(
        stt=prior if slot == "self_stt" else None,
        peer_stt=prior if slot == "peer_stt" else None,
        llm=None,
        osc=Osc(),
    )
    candidate_ref = ResourceRef(f"candidate-{slot}", candidate)
    plan = RuntimeResourceReplacementPlan(
        "retain",
        "replace" if slot == "self_stt" else "retain",
        "replace" if slot == "peer_stt" else "retain",
    )

    class Factory:
        async def build_resources(self, config, requested):  # noqa: ANN001, ANN201
            _ = config
            return StagedRuntimeResources(requested, {slot: candidate_ref})

    runtime = ResolvedRuntimeResourceAdapter(Factory(), hub)
    runtime._active_state = await hub.current_runtime_state()
    runtime._ownership_state_known = True

    class ManagedOwner:
        async def _validate_authoritative(self, receipt) -> None:  # noqa: ANN001
            _ = receipt

    adapter = ManagedAwareResolvedRuntimeAdapter(runtime, ManagedOwner())  # type: ignore[arg-type]
    receipt = SettingsCommitReceipt(AppSettingsVNext(), "1", "test", None)
    request = ResolvedRuntimeActivationRequest(
        CanonicalRuntimeConfigResolver().resolve(receipt), "1", "test", None, receipt
    )
    expected = asyncio.CancelledError if cancel_close else RuntimeCommittedSettlementFailure

    with pytest.raises(expected):
        await adapter.replace_runtime_with_plan(request, plan)

    active = await hub.current_runtime_state()
    assert active.slots[slot].resource is candidate
    assert candidate.close_calls == 0
    assert prior.close_calls == 1
    assert adapter.provider_settlement_owner is not None
    assert len(adapter.provider_settlement_owner.pending) == 1

    with pytest.raises(asyncio.CancelledError if cancel_close else RuntimeError):
        await adapter.close()

    assert prior.close_calls == 2
    assert len(adapter.provider_settlement_owner.pending) == 1

    await adapter.close()

    assert prior.close_calls == 3
    assert adapter.provider_settlement_owner.pending == []
    assert candidate.close_calls == 0
