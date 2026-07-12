from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.app.ports.runtime_resources import ClearSelfSTTResult, InstalledRuntimeState
from puripuly_heart.core.runtime.provider_state import ProviderStateCell
from puripuly_heart.core.runtime.self_audio import (
    SelfChannelConfig,
    SelfChannelState,
    SelfSTTChannelOwner,
    SetSelfSTTEnabled,
)


class Provider:
    def __init__(self) -> None:
        self.close_calls = 0
        self.warmup_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def warmup(self) -> None:
        self.warmup_calls += 1


class Host:
    def __init__(self, cell: ProviderStateCell) -> None:
        self.cell = cell
        self.clear_calls = 0
        self.drain_delays: list[float | None] = []
        self.resume_calls = 0

    def lease_stt_provider(self, slot):  # noqa: ANN001, ANN201
        return self.cell.lease(slot)

    async def clear_self_stt_for_toggle_off(self) -> ClearSelfSTTResult:
        self.clear_calls += 1
        provider = self.cell.snapshot().self_stt.provider
        self.cell.replace("self_stt", None)
        if provider is not None:
            await provider.close()
        return ClearSelfSTTResult(
            active=InstalledRuntimeState({}),
            cleared=provider is not None,
            displaced_identity="self" if provider is not None else None,
        )

    async def drain_self_stt_for_toggle_off(
        self,
        *,
        release_backend_after: float | None = None,
    ) -> None:
        self.drain_delays.append(release_backend_after)

    async def resume_self_stt_after_toggle_on(self) -> None:
        self.resume_calls += 1


class Source:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class Ingress:
    def __init__(self) -> None:
        self.events: list[tuple[object, object]] = []

    async def handle_self_vad_event(self, event: object, provider: object) -> None:
        self.events.append((event, provider))


def make_owner(
    cell: ProviderStateCell, *, fail_source: bool = False, audio_gate=None  # noqa: ANN001
):  # noqa: ANN201
    host = Host(cell)
    ingress = Ingress()
    source = Source()
    sink_ready: asyncio.Future[object] = asyncio.get_running_loop().create_future()

    def source_factory(_config):  # noqa: ANN001, ANN202
        if fail_source:
            raise RuntimeError("capture failed")
        return source

    async def run_audio_loop(**kwargs):  # noqa: ANN003, ANN202
        sink_ready.set_result(kwargs["sink"])
        await asyncio.Event().wait()

    owner = SelfSTTChannelOwner(
        provider_read_port=host,
        provider_host=host,
        ingress=ingress,
        source_factory=source_factory,
        vad_factory=lambda _config: object(),
        run_audio_loop=run_audio_loop,
        audio_gate=audio_gate,
    )
    return owner, host, ingress, source, sink_ready


CONFIG = SelfChannelConfig(16000, ("mic", "en"))
LOCAL_QWEN_CONFIG = SelfChannelConfig(16000, ("mic", "en", "local_qwen"), True)


class Gate:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1


@pytest.mark.asyncio
async def test_enable_uses_current_lease_and_shutdown_does_not_close_provider() -> None:
    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    owner, host, _ingress, source, _sink = make_owner(cell)

    result = await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    assert result.status == "applied"
    assert result.snapshot.state is SelfChannelState.RUNNING
    assert provider.warmup_calls == 1

    await owner.close()
    assert source.close_calls == 1
    assert provider.close_calls == 0
    assert host.clear_calls == 0


@pytest.mark.asyncio
async def test_local_qwen_disable_schedules_owner_idle_release_and_reenable_cancels_it() -> None:
    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    owner, host, _ingress, _source, _sink = make_owner(cell)
    await owner.execute(SetSelfSTTEnabled(True, LOCAL_QWEN_CONFIG))
    await owner.execute(SetSelfSTTEnabled(False))
    assert host.drain_delays == [600.0]
    assert host.clear_calls == 0
    assert cell.snapshot().self_stt.provider is provider
    await owner.execute(SetSelfSTTEnabled(True, LOCAL_QWEN_CONFIG))
    assert host.resume_calls == 2
    await owner.close()


@pytest.mark.asyncio
async def test_gate_resets_before_start_freeze_restart_and_stop() -> None:
    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    gate = Gate()
    owner, _host, _ingress, _source, _sink = make_owner(cell, audio_gate=gate)

    await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    await owner.freeze_for_provider_replacement()
    await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    await owner.execute(SetSelfSTTEnabled(False))

    assert gate.reset_calls == 4


@pytest.mark.asyncio
async def test_guard_failure_resume_restores_self_capture_when_intent_is_unchanged() -> None:
    cell = ProviderStateCell(self_stt=Provider())
    owner, _host, _ingress, _source, _sink = make_owner(cell)
    await owner.execute(SetSelfSTTEnabled(True, CONFIG))

    resume = await owner.freeze_for_provider_replacement()
    assert owner.snapshot().state is SelfChannelState.STOPPED
    await owner.resume_after_provider_replacement(resume)

    snapshot = owner.snapshot()
    assert snapshot.desired_enabled is True
    assert snapshot.state is SelfChannelState.RUNNING
    assert snapshot.intent_generation == resume.intent_generation
    await owner.close()


@pytest.mark.asyncio
async def test_guard_failure_resume_does_not_override_disable_during_attempt() -> None:
    cell = ProviderStateCell(self_stt=Provider())
    owner, _host, _ingress, _source, _sink = make_owner(cell)
    await owner.execute(SetSelfSTTEnabled(True, CONFIG))

    resume = await owner.freeze_for_provider_replacement()
    await owner.execute(SetSelfSTTEnabled(False))
    await owner.resume_after_provider_replacement(resume)

    snapshot = owner.snapshot()
    assert snapshot.desired_enabled is False
    assert snapshot.intent_enabled is False
    assert snapshot.state is SelfChannelState.STOPPED


@pytest.mark.asyncio
async def test_stale_replacement_during_loop_rejects_late_event() -> None:
    first = Provider()
    replacement = Provider()
    cell = ProviderStateCell(self_stt=first)
    owner, _host, ingress, _source, sink_ready = make_owner(cell)
    await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    sink = await sink_ready

    cell.replace("self_stt", replacement)
    await sink.handle_vad_event("late")  # type: ignore[attr-defined]

    assert ingress.events == []
    await owner.close()


@pytest.mark.asyncio
async def test_missing_provider_faults_without_starting_capture() -> None:
    owner, _host, _ingress, source, _sink = make_owner(ProviderStateCell())
    result = await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    assert result.status == "provider_missing"
    assert result.snapshot.state is SelfChannelState.FAULTED
    assert source.close_calls == 0


@pytest.mark.asyncio
async def test_toggle_off_stops_ingress_clears_slot_and_closes_provider_once() -> None:
    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    owner, host, ingress, source, sink_ready = make_owner(cell)
    await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    sink = await sink_ready

    result = await owner.execute(SetSelfSTTEnabled(False))
    await sink.handle_vad_event("late")  # type: ignore[attr-defined]

    assert result.snapshot.state is SelfChannelState.STOPPED
    assert ingress.events == []
    assert source.close_calls == 1
    assert host.clear_calls == 1
    assert cell.snapshot().self_stt.provider is None
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_failed_preparation_preserves_authoritative_provider() -> None:
    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    owner, host, _ingress, _source, _sink = make_owner(cell, fail_source=True)
    result = await owner.execute(SetSelfSTTEnabled(True, CONFIG))
    assert result.status == "preparation_failed"
    assert cell.snapshot().self_stt.provider is provider
    assert provider.close_calls == 0
    assert host.clear_calls == 0


@pytest.mark.asyncio
async def test_production_composition_exposes_only_typed_owner_surfaces() -> None:
    from puripuly_heart.app.wiring_composition import create_self_stt_production_composition

    provider = Provider()
    cell = ProviderStateCell(self_stt=provider)
    host = Host(cell)

    class Lifecycle:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def start_self_audio(self, config: SelfChannelConfig) -> None:
            _ = config
            self.calls.append("start")

        async def stop_self_audio(self) -> None:
            self.calls.append("stop")

    lifecycle = Lifecycle()
    composition = create_self_stt_production_composition(
        host_supplier=lambda: host,
        audio_lifecycle=lifecycle,
        ingress=Ingress(),
    )

    enabled = await composition.commands.execute(SetSelfSTTEnabled(True, CONFIG))
    disabled = await composition.commands.execute(SetSelfSTTEnabled(False))

    assert composition.commands is composition.state
    assert enabled.snapshot.state is SelfChannelState.RUNNING
    assert disabled.snapshot.state is SelfChannelState.STOPPED
    assert lifecycle.calls == ["stop", "start", "stop"]
    assert host.clear_calls == 1
    assert provider.close_calls == 1
