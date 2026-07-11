from __future__ import annotations

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
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.messages import RUNTIME_APPLY_STATUS_APPLIED, RuntimeApplyResult


class Hub:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stop_calls = 0

    async def start(self, *, auto_flush_osc: bool) -> None:
        self.events.append(f"ingress_start:{auto_flush_osc}")

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("host_close")


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

    async def synchronize_startup(self, receipt, operational):  # noqa: ANN001, ANN201
        _ = operational
        self.events.append(f"startup_sync:{receipt.revision}")
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)

    async def resume_peer_stt(self, receipt):  # noqa: ANN001, ANN201
        self.events.append(f"peer:resume:{receipt.revision}")
        return RuntimeApplyResult(RUNTIME_APPLY_STATUS_APPLIED, None, None)


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


def _host(
    events: list[str],
    receipt: SettingsCommitReceipt,
    *,
    unavailable: frozenset[str] = frozenset(),
):
    hub, peer, self_owner, sender = Hub(events), Peer(events), SelfOwner(events), Sender(events)
    composition = Composition(events, unavailable)
    host = ApplicationRuntimeHost(
        parts=ApplicationRuntimeParts(sender, object(), hub, peer, self_owner),
        runtime_composition=composition,
        committed_settings=Receipts(receipt),
        resolver=CanonicalRuntimeConfigResolver(),
    )
    return host, hub, peer, composition.surface_transactions


@pytest.mark.asyncio
async def test_production_host_installs_canonical_providers_before_starting_ingress() -> None:
    events: list[str] = []
    receipt = SettingsCommitReceipt(
        AppSettingsVNext(), "sha256:authoritative", "startup", "startup-correlation"
    )
    host, _hub, _peer, _transactions = _host(events, receipt)

    await host.start()

    assert events == [
        "providers_install:sha256:authoritative:llm",
        "providers_install:sha256:authoritative:self_stt",
        "providers_install:sha256:authoritative:peer_stt",
        "startup_sync:sha256:authoritative",
        "ingress_start:True",
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
    )

    await host.start()

    assert events[-1] == "ingress_start:True"
    assert "providers_install:actual-revision:llm" in events
    assert "providers_install:actual-revision:peer_stt" in events


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
    assert "audio_gate=composition.audio_gate" in main_source
    assert "_ControllerProviderRuntimeApply" not in stt_route
    assert "_apply_provider_runtime_plan" not in stt_route


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
    assert synchronization.calls == 0
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
