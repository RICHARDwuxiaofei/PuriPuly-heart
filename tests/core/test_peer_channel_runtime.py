from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from puripuly_heart.config.process_capture_resolution import ProcessCaptureTargetUnavailableError
from puripuly_heart.config.resolved import ResolvedDesktopAudioCaptureTarget, ResolvedSTTConfig
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.runtime.peer_channel import (
    PeerChannelRuntime,
    PeerChannelRuntimeState,
    PeerProviderReleaseOutcome,
    PeerRuntimeConfig,
    PeerRuntimeFailureReason,
)
from puripuly_heart.core.runtime.provider_state import ProviderStateCell


@dataclass(slots=True)
class Provider:
    events: list[object] = field(default_factory=list)
    close_calls: int = 0
    warmup_calls: int = 0

    async def handle_vad_event(self, event: object) -> None:
        self.events.append(event)

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class Source:
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1


class HostReadPort:
    def __init__(self, provider: Provider | None) -> None:
        self.state = ProviderStateCell(peer_stt=provider)
        self.deliver_started = asyncio.Event()
        self.deliver_release = asyncio.Event()
        self.block_delivery = False
        self.start_calls = 0
        self.drain_delays: list[float | None] = []
        self.release_failure: BaseException | None = None
        self.release_raises = False
        self.release_calls = 0
        self.release_started = asyncio.Event()
        self.release_continue = asyncio.Event()
        self.block_release = False

    def lease_stt_provider(self, slot):  # noqa: ANN001, ANN201
        return self.state.lease(slot)

    async def handle_peer_vad_event(self, event, *, stt_provider=None) -> None:  # noqa: ANN001
        if self.block_delivery:
            self.deliver_started.set()
            await self.deliver_release.wait()
        await stt_provider.handle_vad_event(event)

    async def ensure_peer_provider_ingress(self, _provider) -> None:  # noqa: ANN001
        self.start_calls += 1

    async def release_peer_provider_ingress(self, _provider):  # noqa: ANN001, ANN201
        self.release_calls += 1
        self.release_started.set()
        if self.block_release:
            await self.release_continue.wait()
        if self.release_raises and self.release_failure is not None:
            raise self.release_failure
        return PeerProviderReleaseOutcome(self.release_failure)

    async def drain_peer_stt_for_toggle_off(
        self,
        _provider,
        *,
        release_backend_after: float | None = None,
    ) -> None:  # noqa: ANN001
        self.drain_delays.append(release_backend_after)


class LoopDriver:
    def __init__(self) -> None:
        self.sink = None
        self.ready = asyncio.Event()

    async def __call__(self, **kwargs) -> None:  # noqa: ANN003
        self.sink = kwargs["sink"]
        self.ready.set()
        await asyncio.Event().wait()


def config() -> PeerRuntimeConfig:
    backend = ResolvedSTTConfig.__new__(ResolvedSTTConfig)
    object.__setattr__(backend, "sample_rate_hz", 16000)
    return PeerRuntimeConfig(
        backend=backend,
        output_device="loopback",
        vad_threshold=0.6,
        vad_hangover_ms=900,
        vad_pre_roll_ms=500,
        provider_signature=("provider",),
        runtime_signature=("loopback", 0.6, 900, 500),
    )


def local_qwen_config() -> PeerRuntimeConfig:
    result = config()
    object.__setattr__(result.backend, "provider", "local_qwen")
    return result


def process_config() -> PeerRuntimeConfig:
    result = config()
    return PeerRuntimeConfig(
        backend=result.backend,
        output_device=result.output_device,
        vad_threshold=result.vad_threshold,
        vad_hangover_ms=result.vad_hangover_ms,
        vad_pre_roll_ms=result.vad_pre_roll_ms,
        provider_signature=result.provider_signature,
        runtime_signature=("process",),
        capture_target=ResolvedDesktopAudioCaptureTarget(
            kind="process",
            process_kind="vrchat",
            executable_identity=r"c:\vrchat\vrchat.exe",
        ),
    )


def runtime(host: HostReadPort, loop: LoopDriver, sources: list[Source]) -> PeerChannelRuntime:
    return PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: sources.append(Source()) or sources[-1],
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=loop,
    )


@pytest.mark.asyncio
async def test_current_peer_lease_receives_vad_and_warmup() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    peer = runtime(host, loop, [])
    await peer.apply_policy(config=config(), desired_active=True)
    await loop.ready.wait()

    await loop.sink.handle_vad_event("speech")
    await peer.warmup()

    assert provider.events == ["speech"]
    assert provider.warmup_calls == 1
    await peer.close()


@pytest.mark.asyncio
async def test_local_qwen_disable_schedules_idle_release_and_reenable_cancels_it() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    peer = runtime(host, loop, [])
    local = local_qwen_config()
    await peer.apply_policy(config=local, desired_active=True)
    await peer.apply_policy(config=local, desired_active=False)
    assert host.drain_delays == [600.0]
    assert host.state.snapshot().peer_stt.provider is provider
    await peer.apply_policy(config=local, desired_active=True)
    assert host.start_calls == 2
    await peer.close()


@pytest.mark.asyncio
async def test_replacement_during_loop_makes_old_lease_stale_and_next_event_uses_new() -> None:
    old = Provider()
    new = Provider()
    host = HostReadPort(old)
    host.block_delivery = True
    loop = LoopDriver()
    peer = runtime(host, loop, [])
    await peer.apply_policy(config=config(), desired_active=True)
    await loop.ready.wait()
    delivery = asyncio.create_task(loop.sink.handle_vad_event("old"))
    await host.deliver_started.wait()
    host.state.replace("peer_stt", new)
    host.deliver_release.set()
    await delivery
    host.block_delivery = False

    await loop.sink.handle_vad_event("new")

    assert old.events == ["old"]
    assert new.events == ["new"]
    assert peer.state == PeerChannelRuntimeState.RUNNING
    await peer.close()


@pytest.mark.asyncio
async def test_missing_provider_faults_without_starting_audio() -> None:
    host = HostReadPort(None)
    loop = LoopDriver()
    sources: list[Source] = []
    peer = runtime(host, loop, sources)

    await peer.apply_policy(config=config(), desired_active=True)

    assert peer.state == PeerChannelRuntimeState.FAULTED
    assert sources == []


@pytest.mark.asyncio
async def test_process_failure_publishes_typed_warning_and_explicit_retry_recovers() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    diagnostics = []
    attempts = 0

    def source_factory(_config):  # noqa: ANN001, ANN202
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProcessCaptureTargetUnavailableError("no_process")
        return Source()

    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=source_factory,
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=LoopDriver(),
        diagnostic_sink=diagnostics.append,
    )
    process = process_config()
    await peer.apply_policy(config=process, desired_active=True)
    assert peer.state is PeerChannelRuntimeState.FAULTED
    assert diagnostics[-1].reason is PeerRuntimeFailureReason.PROCESS_TARGET_UNAVAILABLE
    assert diagnostics[-1].process_unavailable_reason == "no_process"
    assert await peer.retry_process_capture(config=process) is True
    assert peer.state is PeerChannelRuntimeState.RUNNING
    assert peer.last_failure is None
    await peer.close()


@pytest.mark.asyncio
async def test_faulted_process_ignores_all_ordinary_policy_reapplication() -> None:
    host = HostReadPort(Provider())
    attempts = 0

    def source_factory(_config):  # noqa: ANN001, ANN202
        nonlocal attempts
        attempts += 1
        raise ProcessCaptureTargetUnavailableError("no_process")

    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=source_factory,
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=LoopDriver(),
    )
    process = process_config()
    await peer.apply_policy(config=process, desired_active=True)
    await peer.apply_policy(config=process, desired_active=True)
    await peer.apply_policy(config=process, desired_active=True, record_intent=False)
    await peer.apply_policy(config=process, desired_active=True, record_intent=False)

    assert attempts == 1
    assert peer.state is PeerChannelRuntimeState.FAULTED
    await peer.close()


@pytest.mark.asyncio
async def test_process_target_exit_terminalizes_before_typed_warning() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    diagnostics = []

    class ExitedSource(Source):
        terminal_reason = "target_exited"

    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: ExitedSource(),
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=lambda **_kwargs: asyncio.sleep(0),
        diagnostic_sink=diagnostics.append,
    )
    await peer.apply_policy(config=process_config(), desired_active=True)
    for _ in range(10):
        if peer.state is PeerChannelRuntimeState.FAULTED:
            break
        await asyncio.sleep(0)
    assert peer.state is PeerChannelRuntimeState.FAULTED
    assert diagnostics[-1].reason is PeerRuntimeFailureReason.PROCESS_TARGET_EXITED
    assert peer.loop_task is None
    await peer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "release_failure", [RuntimeError("release failed"), asyncio.CancelledError()]
)
async def test_provider_release_failure_settles_before_terminal_warning(
    release_failure: BaseException,
) -> None:
    host = HostReadPort(Provider())
    host.release_failure = release_failure
    host.release_raises = True
    diagnostics = []

    class ExitedSource(Source):
        terminal_reason = "target_exited"

    source = ExitedSource()
    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: source,
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=lambda **_kwargs: asyncio.sleep(0),
        diagnostic_sink=diagnostics.append,
    )
    await peer.apply_policy(config=process_config(), desired_active=True)
    for _ in range(10):
        if diagnostics:
            break
        await asyncio.sleep(0)

    assert peer.state is PeerChannelRuntimeState.FAULTED
    assert source.close_calls == 1
    assert peer.loop_task is None
    assert peer.last_provider_release_failure is release_failure
    assert diagnostics[-1].reason is PeerRuntimeFailureReason.PROCESS_TARGET_EXITED
    await peer.close()


@pytest.mark.asyncio
async def test_blocked_release_terminalizes_policy_and_orders_explicit_retry() -> None:
    host = HostReadPort(Provider())
    host.block_release = True

    class ExitedSource(Source):
        terminal_reason = "target_exited"

    first = ExitedSource()
    retried = Source()
    sources = [first, retried]
    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: sources.pop(0),
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=lambda **_kwargs: asyncio.sleep(0),
    )
    process = process_config()
    await peer.apply_policy(config=process, desired_active=True)
    await host.release_started.wait()

    assert peer.state is PeerChannelRuntimeState.FINALIZING
    assert peer.current_signature is None
    await peer.apply_policy(config=process, desired_active=True)
    await peer.apply_policy(config=process, desired_active=True, record_intent=False)
    assert len(sources) == 1

    retry_task = asyncio.create_task(peer.retry_process_capture(config=process))
    await asyncio.sleep(0)
    assert not retry_task.done()
    host.release_continue.set()
    assert await retry_task is True
    assert peer.state is PeerChannelRuntimeState.RUNNING
    assert first.close_calls == 1
    assert retried.close_calls == 0
    await peer.close()


@pytest.mark.asyncio
async def test_failed_replacement_preserves_current_lease() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    lease = host.lease_stt_provider("peer_stt")

    try:
        raise RuntimeError("candidate preparation failed")
    except RuntimeError:
        pass

    assert lease is not None
    assert lease.current is provider
    assert host.lease_stt_provider("peer_stt").current is provider


@pytest.mark.asyncio
async def test_peer_shutdown_closes_audio_but_not_host_provider() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    sources: list[Source] = []
    peer = runtime(host, loop, sources)
    await peer.apply_policy(config=config(), desired_active=True)

    await peer.close()

    assert sources[0].close_calls == 1


@pytest.mark.asyncio
async def test_user_disable_during_provider_install_prevents_resume() -> None:
    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    sources: list[Source] = []
    peer = runtime(host, loop, sources)
    await peer.apply_policy(config=config(), desired_active=True)
    await loop.ready.wait()

    resume = await peer.freeze_for_provider_replacement()
    await peer.apply_policy(config=config(), desired_active=False)
    await peer.resume_after_provider_replacement(resume)

    assert peer.state == PeerChannelRuntimeState.STOPPED
    assert len(sources) == 1
    assert sources[0].close_calls == 1
    assert provider.close_calls == 0


@pytest.mark.asyncio
async def test_failed_source_close_is_not_retried_until_later_teardown() -> None:
    class FlakySource(Source):
        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("blocking close failed")

    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    source = FlakySource()
    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: source,
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=loop,
    )
    await peer.apply_policy(config=config(), desired_active=True)

    with pytest.raises(RuntimeError, match="blocking close failed"):
        await peer.close()
    assert source.close_calls == 1

    await peer.close()
    assert source.close_calls == 2


@pytest.mark.asyncio
async def test_concurrent_supersede_during_blocking_close_closes_source_identity_once() -> None:
    class BlockingSource(Source):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.started.set()
            await self.release.wait()

    provider = Provider()
    host = HostReadPort(provider)
    loop = LoopDriver()
    source = BlockingSource()
    peer = PeerChannelRuntime(
        hub=host,
        clock=FakeClock(),
        provider_read_port=host,
        provider_ingress_port=host,
        source_factory=lambda _config: source,
        vad_factory=lambda _config, _path: object(),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=loop,
    )
    await peer.apply_policy(config=config(), desired_active=True)

    freeze = asyncio.create_task(peer.freeze_for_provider_replacement())
    await source.started.wait()
    disable = asyncio.create_task(peer.apply_policy(config=config(), desired_active=False))
    source.release.set()
    await asyncio.gather(freeze, disable)

    assert source.close_calls == 1
    assert peer.state == PeerChannelRuntimeState.STOPPED


@pytest.mark.asyncio
async def test_host_shutdown_closes_peer_provider_exactly_once() -> None:
    provider = Provider()
    hub = ClientHub(stt=None, llm=None, peer_stt=provider, osc=object())

    await hub.stop()
    await hub.stop()

    assert provider.close_calls == 1


def test_peer_owner_inventory_excludes_provider_authority() -> None:
    host = HostReadPort(Provider())
    peer = runtime(host, LoopDriver(), [])

    snapshot = peer.lifecycle_owner_snapshot()

    assert "_stt" not in snapshot["resource_fields"]
    assert "provider" not in snapshot["shutdown_policy"]
