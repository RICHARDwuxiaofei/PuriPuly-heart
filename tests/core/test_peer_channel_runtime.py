from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from puripuly_heart.config.process_capture_platform import ProcessCapturePlatformAvailability
from puripuly_heart.config.process_capture_resolution import ProcessCaptureTargetUnavailableError
from puripuly_heart.config.resolved import (
    CREDENTIAL_SOURCE_SECRET_STORE,
    ResolvedCredentialRequirement,
    ResolvedDesktopAudioCaptureTarget,
    ResolvedSTTConfig,
)
from puripuly_heart.config.settings import STTProviderName
from puripuly_heart.config.settings_vnext.schema import ProcessCaptureTargetIntent
from puripuly_heart.core.audio.process_identity import PsutilProcessIdentityWatcher
from puripuly_heart.core.audio.process_source import (
    ProcessAudioCaptureSource,
    ResolvedProcessCaptureIdentity,
)
from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.runtime.peer_channel import (
    PeerChannelRuntime,
    PeerChannelRuntimeState,
    PeerRuntimeConfig,
    PeerRuntimeFailureReason,
)
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle


@dataclass(slots=True)
class DummyManagedSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class DummyBackendClosingSTT:
    close_calls: int = 0
    close_backend_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def close_backend(self) -> None:
        self.close_backend_calls += 1


@dataclass(slots=True)
class RetriableBackendClosingSTT:
    name: str = "peer"
    close_backend_failures: int = 0
    close_calls: int = 0
    close_backend_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def close_backend(self) -> None:
        self.close_backend_calls += 1
        if self.close_backend_failures > 0:
            self.close_backend_failures -= 1
            raise RuntimeError(f"{self.name} backend close failed")


@dataclass(slots=True)
class BlockingEventingSTT:
    name: str
    block_backend_close: bool = False
    close_backend_failures: int = 0
    close_backend_calls: int = 0
    event_queue: asyncio.Queue[object] = field(default_factory=asyncio.Queue)
    close_backend_started: asyncio.Event = field(default_factory=asyncio.Event)
    close_backend_release: asyncio.Event = field(default_factory=asyncio.Event)

    async def events(self):
        while True:
            yield await self.event_queue.get()

    async def close_backend(self) -> None:
        self.close_backend_calls += 1
        self.close_backend_started.set()
        if self.block_backend_close:
            await self.close_backend_release.wait()
        if self.close_backend_failures > 0:
            self.close_backend_failures -= 1
            raise RuntimeError(f"{self.name} backend close failed")


@dataclass(slots=True)
class DummySource:
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class BlockingCloseSource:
    name: str
    block_on_close: bool = False
    close_calls: int = 0
    close_started: asyncio.Event = field(default_factory=asyncio.Event)
    close_release: asyncio.Event = field(default_factory=asyncio.Event)

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        if self.block_on_close:
            await self.close_release.wait()


@dataclass(slots=True)
class FailingCloseSource:
    failure: Exception
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1
        raise self.failure


@dataclass(slots=True)
class RetriableCloseSource:
    name: str = "source"
    close_failures: int = 0
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_failures > 0:
            self.close_failures -= 1
            raise RuntimeError(f"{self.name} close failed")


@dataclass(slots=True)
class BlockingWarmupSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0
    warmup_started: asyncio.Event = field(default_factory=asyncio.Event)
    warmup_release: asyncio.Event = field(default_factory=asyncio.Event)

    async def warmup(self) -> None:
        self.warmup_calls += 1
        self.warmup_started.set()
        await self.warmup_release.wait()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class FailureAwareSTT:
    name: str = "peer"
    warmup_calls: int = 0
    close_calls: int = 0
    on_terminal_failure: object | None = None

    async def warmup(self) -> None:
        self.warmup_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def trigger_failure(self, exc: Exception) -> None:
        assert self.on_terminal_failure is not None
        await self.on_terminal_failure(exc)


@dataclass(slots=True)
class TerminalFailureProvider:
    on_terminal_failure: object | None = None
    close_backend_calls: int = 0

    async def close_backend(self) -> None:
        self.close_backend_calls += 1

    async def trigger_failure(self) -> None:
        assert self.on_terminal_failure is not None
        await self.on_terminal_failure(RuntimeError("provider terminal failure"))


class DummyHub:
    def __init__(self) -> None:
        self.peer_stt = None
        self.replace_peer_stt_calls: list[object | None] = []
        self.peer_events: list[object] = []

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self.replace_peer_stt_calls.append(stt)
        self.peer_stt = stt

    async def handle_peer_vad_event(self, event: object) -> None:
        self.peer_events.append(event)


class FailingDetachHub(DummyHub):
    def __init__(self, failure: Exception) -> None:
        super().__init__()
        self.failure = failure

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self.replace_peer_stt_calls.append(stt)
        if stt is None:
            raise self.failure
        self.peer_stt = stt


class StagedAttachHub(DummyHub):
    def __init__(self) -> None:
        super().__init__()
        self.first_attach_started = asyncio.Event()
        self.first_attach_release = asyncio.Event()
        self._attach_calls = 0

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self._attach_calls += 1
        self.replace_peer_stt_calls.append(stt)
        self.peer_stt = stt
        if self._attach_calls == 1 and stt is not None:
            self.first_attach_started.set()
            await self.first_attach_release.wait()


class ProviderHandleBackedHub(DummyHub):
    def __init__(self) -> None:
        super().__init__()
        self._peer_stt_provider_runtime = ProviderRuntimeHandle(
            name="peer_stt",
            state_changed=self._sync_peer_stt_alias,
        )

    def _sync_peer_stt_alias(self, _handle: ProviderRuntimeHandle | None = None) -> None:
        self.peer_stt = self._peer_stt_provider_runtime.provider

    async def replace_peer_stt_provider(self, stt: object | None) -> None:
        self.replace_peer_stt_calls.append(stt)
        await self._peer_stt_provider_runtime.stop_ingress()
        await self._peer_stt_provider_runtime.replace_provider(stt, start=False)
        self._sync_peer_stt_alias()

    async def close(self) -> None:
        await self._peer_stt_provider_runtime.close()
        self._sync_peer_stt_alias()


class EventIngressProviderHandleBackedHub(DummyHub):
    def __init__(self) -> None:
        super().__init__()
        self._peer_stt_provider_runtime = ProviderRuntimeHandle(
            name="peer_stt",
            event_handler=self._handle_peer_stt_event,
            state_changed=self._sync_peer_stt_alias,
        )

    def _sync_peer_stt_alias(self, _handle: ProviderRuntimeHandle | None = None) -> None:
        self.peer_stt = self._peer_stt_provider_runtime.provider

    async def _handle_peer_stt_event(self, event: object) -> None:
        self.peer_events.append(event)

    async def replace_peer_stt_provider(
        self,
        stt: object | None,
        *,
        start: bool = True,
    ) -> None:
        self.replace_peer_stt_calls.append(stt)
        await self._peer_stt_provider_runtime.stop_ingress()
        await self._peer_stt_provider_runtime.replace_provider(stt, start=start)
        self._sync_peer_stt_alias()

    async def start_peer_stt_provider_ingress(self, stt: object) -> None:
        if self.peer_stt is not stt:
            return
        await self._peer_stt_provider_runtime.start()
        self._sync_peer_stt_alias()

    async def close(self) -> None:
        await self._peer_stt_provider_runtime.close()
        self._sync_peer_stt_alias()


async def fake_run_audio_loop(**_kwargs) -> None:
    await asyncio.Event().wait()


class ExceptionObservingTask(asyncio.Task):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.exception_requests = 0

    def exception(self) -> BaseException | None:
        self.exception_requests += 1
        return super().exception()


async def wait_until(predicate, *, timeout_s: float = 1.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0)


def make_peer_runtime_config(output_device: str = "Headphones (Loopback)") -> PeerRuntimeConfig:
    backend = ResolvedSTTConfig(
        channel="peer",
        provider=STTProviderName.DEEPGRAM,
        source_language="ko",
        model="nova-3",
        endpoint=None,
        region=None,
        credential=ResolvedCredentialRequirement(
            source=CREDENTIAL_SOURCE_SECRET_STORE,
            required=True,
            reference="deepgram:stt",
        ),
        input_host_api=None,
        input_device=None,
        output_device=output_device,
        sample_rate_hz=16000,
        channels=1,
        ring_buffer_ms=500,
        drain_timeout_s=2.0,
        vad_speech_threshold=0.6,
        vad_hangover_ms=900,
        vad_pre_roll_ms=500,
        low_latency_enabled=True,
        low_latency_merge_gap_ms=600,
        low_latency_spec_retry_max=10,
        custom_vocabulary_enabled=True,
        custom_terms={"ko": ("아이리", "시나노")},
        provider_options={},
    )
    provider_signature = (
        backend.provider,
        backend.source_language,
        backend.model,
        backend.custom_terms,
    )
    return PeerRuntimeConfig(
        backend=backend,
        output_device=output_device,
        vad_threshold=0.6,
        vad_hangover_ms=900,
        vad_pre_roll_ms=500,
        provider_signature=provider_signature,
        runtime_signature=(
            backend.source_language,
            output_device,
            0.6,
            900,
            500,
            provider_signature,
        ),
    )


def make_process_peer_runtime_config() -> PeerRuntimeConfig:
    config = make_peer_runtime_config()
    target = ResolvedDesktopAudioCaptureTarget(
        kind="process",
        process_kind="generic_executable",
        executable_identity=r"c:\apps\game\game.exe",
    )
    return PeerRuntimeConfig(
        backend=config.backend,
        output_device="",
        vad_threshold=config.vad_threshold,
        vad_hangover_ms=config.vad_hangover_ms,
        vad_pre_roll_ms=config.vad_pre_roll_ms,
        provider_signature=config.provider_signature,
        runtime_signature=("process", target),
        capture_target=target,
    )


@pytest.mark.asyncio
async def test_process_unavailable_never_routes_to_device_and_requires_explicit_retry() -> None:
    hub = DummyHub()
    attempted_targets: list[ResolvedDesktopAudioCaptureTarget] = []
    diagnostics = []

    def source_factory(config: PeerRuntimeConfig) -> DummySource:
        attempted_targets.append(config.capture_target)
        raise ProcessCaptureTargetUnavailableError("no_process")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyBackendClosingSTT(),
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
        diagnostic_sink=diagnostics.append,
    )
    config = make_process_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await runtime.apply_policy(config=config, desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert attempted_targets == [config.capture_target]
    assert runtime.last_failure is not None
    assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_TARGET_UNAVAILABLE
    assert runtime.last_failure.process_unavailable_reason == "no_process"
    assert diagnostics == [runtime.last_failure]
    assert hub.peer_stt is None

    assert await runtime.retry_process_capture(config=config) is False
    assert attempted_targets == [config.capture_target, config.capture_target]


@pytest.mark.asyncio
async def test_process_target_exit_tears_down_source_provider_and_owned_loop_without_reconnect() -> (
    None
):
    events: list[str] = []

    class OrderedHub(DummyHub):
        async def replace_peer_stt_provider(self, stt: object | None) -> None:
            events.append("provider_detached" if stt is None else "provider_attached")
            await super().replace_peer_stt_provider(stt)

    class TerminalSource(DummySource):
        terminal_reason = "target_exited"

        async def close(self) -> None:
            events.append("source_closed")
            await super().close()

    hub = OrderedHub()
    created_sources: list[TerminalSource] = []

    def source_factory(config: PeerRuntimeConfig) -> TerminalSource:
        assert config.capture_target.kind == "process"
        source = TerminalSource()
        created_sources.append(source)
        return source

    async def completed_loop(**_kwargs) -> None:
        return None

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=completed_loop,
    )
    config = make_process_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await wait_until(lambda: runtime.state == PeerChannelRuntimeState.FAULTED)
    await asyncio.sleep(0)
    await runtime.apply_policy(config=config, desired_active=True)

    assert runtime.last_failure is not None
    assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_TARGET_EXITED
    assert events == [
        "provider_attached",
        "provider_detached",
        "source_closed",
        "provider_detached",
    ]
    assert created_sources[0].close_calls == 1
    assert len(created_sources) == 1


@pytest.mark.asyncio
async def test_process_fault_completes_teardown_when_diagnostic_sink_fails() -> None:
    hub = DummyHub()
    stt = DummyBackendClosingSTT()
    source = DummySource()
    sink_observations: list[tuple[int, object | None]] = []

    def source_factory(config: PeerRuntimeConfig) -> DummySource:
        _ = config
        raise ProcessCaptureTargetUnavailableError("no_process")

    def failing_sink(_diagnostic: object) -> None:
        sink_observations.append((stt.close_backend_calls, hub.peer_stt))
        raise RuntimeError("sink failed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: stt,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
        diagnostic_sink=failing_sink,
    )

    await runtime.apply_policy(config=make_process_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert stt.close_backend_calls == 1
    assert source.close_calls == 0
    assert hub.peer_stt is None
    assert sink_observations == [(1, None)]


@pytest.mark.asyncio
async def test_process_provider_replacement_failure_faults_and_does_not_reconnect() -> None:
    class AttachThenFailHub(DummyHub):
        async def replace_peer_stt_provider(self, stt: object | None) -> None:
            self.replace_peer_stt_calls.append(stt)
            self.peer_stt = stt
            if stt is not None:
                raise RuntimeError("attach failed")

    hub = AttachThenFailHub()
    source = DummySource()
    diagnostics = []
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyBackendClosingSTT(),
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
        diagnostic_sink=diagnostics.append,
    )
    config = make_process_peer_runtime_config()

    with pytest.raises(RuntimeError, match="attach failed"):
        await runtime.apply_policy(config=config, desired_active=True)
    replacement_calls_after_fault = len(hub.replace_peer_stt_calls)
    await runtime.apply_policy(config=config, desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert source.close_calls == 1
    assert hub.peer_stt is None
    assert runtime.last_failure is not None
    assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_PROVIDER_FAILED
    assert diagnostics == [runtime.last_failure]
    assert hub.replace_peer_stt_calls[0] is not None
    assert all(call is None for call in hub.replace_peer_stt_calls[1:])
    assert len(hub.replace_peer_stt_calls) == replacement_calls_after_fault


@pytest.mark.asyncio
async def test_process_provider_terminal_failure_tears_down_and_recovers_only_by_explicit_retry() -> (
    None
):
    class ClosingHub(DummyHub):
        async def replace_peer_stt_provider(self, stt: object | None) -> None:
            previous = self.peer_stt
            self.replace_peer_stt_calls.append(stt)
            self.peer_stt = stt
            if previous is not None and previous is not stt:
                close_backend = getattr(previous, "close_backend", None)
                if callable(close_backend):
                    await close_backend()

    hub = ClosingHub()
    providers: list[TerminalFailureProvider] = []
    sources: list[DummySource] = []
    loop_stop = asyncio.Event()
    observations: list[tuple[int, int, bool]] = []
    task: asyncio.Task[None] | None = None

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = config
        provider = TerminalFailureProvider(on_terminal_failure=on_terminal_failure)
        providers.append(provider)
        return provider

    def source_factory(config: PeerRuntimeConfig) -> DummySource:
        _ = config
        source = DummySource()
        sources.append(source)
        return source

    async def waiting_loop(**_kwargs) -> None:
        await loop_stop.wait()

    def sink(_diagnostic: object) -> None:
        assert task is not None
        observations.append((sources[0].close_calls, providers[0].close_backend_calls, task.done()))

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=waiting_loop,
        diagnostic_sink=sink,
    )
    config = make_process_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    task = runtime.loop_task
    assert task is not None
    await providers[0].trigger_failure()
    await runtime.apply_policy(config=config, desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert runtime.last_failure is not None
    assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_PROVIDER_FAILED
    assert sources[0].close_calls == 1
    assert providers[0].close_backend_calls == 1
    assert observations == [(1, 1, True)]
    assert len(sources) == 1
    assert await runtime.retry_process_capture(config=config) is True
    assert len(sources) == 2

    loop_stop.set()
    await runtime.close()


@pytest.mark.asyncio
async def test_process_provider_terminal_fault_stops_real_identity_watch_thread_while_target_alive() -> (
    None
):
    psutil = pytest.importorskip("psutil")

    @dataclass
    class CaptureStub:
        on_data: object
        started: bool = False
        closed: bool = False

        def start(self) -> None:
            self.started = True

        def close(self) -> None:
            self.closed = True

    @dataclass
    class CaptureFactory:
        captures: list[CaptureStub] = field(default_factory=list)

        def create(self, *, pid: int, on_data):  # noqa: ANN001
            _ = pid
            capture = CaptureStub(on_data=on_data)
            self.captures.append(capture)
            return capture

    class ClosingHub(DummyHub):
        async def replace_peer_stt_provider(self, stt: object | None) -> None:
            previous = self.peer_stt
            self.replace_peer_stt_calls.append(stt)
            self.peer_stt = stt
            if previous is not None and previous is not stt:
                close_backend = getattr(previous, "close_backend", None)
                if callable(close_backend):
                    await close_backend()

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process = psutil.Process(child.pid)
    identity = ResolvedProcessCaptureIdentity(
        pid=child.pid,
        target=ProcessCaptureTargetIntent.generic_executable(r"C:\Apps\Game\Game.exe"),
        instance_id=f"{child.pid}:{process.create_time()}",
    )
    capture_factory = CaptureFactory()
    watches: list[object] = []
    providers: list[TerminalFailureProvider] = []
    loop_stop = asyncio.Event()

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = config
        provider = TerminalFailureProvider(on_terminal_failure=on_terminal_failure)
        providers.append(provider)
        return provider

    def source_factory(config: PeerRuntimeConfig) -> ProcessAudioCaptureSource:
        _ = config
        source = ProcessAudioCaptureSource(
            identity=identity,
            watcher=PsutilProcessIdentityWatcher(),
            capture_factory=capture_factory,
            platform_availability=lambda: ProcessCapturePlatformAvailability(available=True),
        )
        watches.append(source._watch)
        return source

    async def waiting_loop(**_kwargs) -> None:
        await loop_stop.wait()

    runtime = PeerChannelRuntime(
        hub=ClosingHub(),
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=waiting_loop,
    )
    config = make_process_peer_runtime_config()
    try:
        await runtime.apply_policy(config=config, desired_active=True)
        assert watches
        assert watches[0].watch_thread_alive is True  # type: ignore[attr-defined]

        await providers[0].trigger_failure()
        await wait_until(lambda: runtime.state == PeerChannelRuntimeState.FAULTED)
        await asyncio.sleep(0)

        assert runtime.last_failure is not None
        assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_PROVIDER_FAILED
        assert watches[0].watch_thread_alive is False  # type: ignore[attr-defined]
        assert capture_factory.captures[0].closed is True
        assert child.poll() is None
        assert await runtime.retry_process_capture(config=config) is True
        assert len(watches) == 2
    finally:
        loop_stop.set()
        await runtime.close()
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.asyncio
async def test_current_peer_loop_fault_emits_diagnostic_only_after_loop_exit() -> None:
    class ClosingHub(DummyHub):
        async def replace_peer_stt_provider(self, stt: object | None) -> None:
            previous = self.peer_stt
            self.replace_peer_stt_calls.append(stt)
            self.peer_stt = stt
            if previous is not None and previous is not stt:
                await previous.close_backend()

    hub = ClosingHub()
    source = DummySource()
    provider = DummyBackendClosingSTT()
    observations: list[tuple[int, int, bool]] = []
    task: asyncio.Task[None] | None = None

    async def failing_loop(**_kwargs) -> None:
        raise RuntimeError("source failed")

    def sink(_diagnostic: object) -> None:
        assert task is not None
        observations.append((source.close_calls, provider.close_backend_calls, task.done()))

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: provider,
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_loop,
        diagnostic_sink=sink,
    )

    await runtime.apply_policy(config=make_process_peer_runtime_config(), desired_active=True)
    task = runtime.loop_task
    assert task is not None
    await wait_until(task.done)
    await asyncio.sleep(0)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert runtime.last_failure is not None
    assert runtime.last_failure.reason is PeerRuntimeFailureReason.PROCESS_SOURCE_FAILED
    assert observations == [(1, 1, True)]


@pytest.mark.asyncio
async def test_apply_policy_is_idempotent_for_same_runtime_signature() -> None:
    hub = DummyHub()
    created: list[str] = []

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: created.append("stt") or DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await runtime.apply_policy(config=config, desired_active=True)

    assert created == ["stt"]
    assert len(hub.replace_peer_stt_calls) == 1
    assert runtime.state == PeerChannelRuntimeState.RUNNING


def test_peer_channel_runtime_exposes_named_owner_inventory_and_policies() -> None:
    runtime = PeerChannelRuntime(
        hub=DummyHub(),
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    snapshot_method = getattr(runtime, "lifecycle_owner_snapshot", None)

    assert callable(snapshot_method)
    snapshot = snapshot_method()
    assert snapshot["owner"] == "PeerChannelRuntime"
    assert snapshot["resource_fields"] == (
        "_stt",
        "_audio_source",
        "_vad",
        "_loop_task",
        "_generation",
        "_desired_active",
        "_lock",
    )
    assert snapshot["stop_ingress"] == "invalidate generation and desired-active state"
    assert "cancel loop" in snapshot["shutdown_policy"]
    assert "late peer callbacks" in snapshot["late_callback_rule"]


@pytest.mark.asyncio
async def test_same_signature_reapply_still_auto_recovers_late_terminal_failure() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await runtime.apply_policy(config=config, desired_active=True)
    await created[0].trigger_failure(RuntimeError("peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.RUNNING
    assert len(created) == 1
    assert hub.peer_stt is created[0]


@pytest.mark.asyncio
async def test_stale_generation_teardown_does_not_detach_newer_peer_provider() -> None:
    hub = StagedAttachHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = on_terminal_failure
        stt = FailureAwareSTT(name=config.output_device)
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    first_task = asyncio.create_task(runtime.apply_policy(config=first, desired_active=True))
    await hub.first_attach_started.wait()
    second_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    await second_task
    hub.first_attach_release.set()
    await first_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"
    assert runtime.current_signature == second.runtime_signature


@pytest.mark.asyncio
async def test_superseded_reconfigure_does_not_replace_newer_peer_provider_after_old_cleanup() -> (
    None
):
    hub = ProviderHandleBackedHub()
    created_stt: list[RetriableBackendClosingSTT] = []
    sources: dict[str, BlockingCloseSource] = {}

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = RetriableBackendClosingSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> BlockingCloseSource:
        source = BlockingCloseSource(
            name=config.output_device,
            block_on_close=config.output_device == "first-device",
        )
        sources[config.output_device] = source
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")
    third = make_peer_runtime_config(output_device="third-device")

    await runtime.apply_policy(config=first, desired_active=True)
    try:
        second_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
        await sources["first-device"].close_started.wait()

        await runtime.apply_policy(config=third, desired_active=True)
        third_stt = created_stt[2]
        assert hub.peer_stt is third_stt

        sources["first-device"].close_release.set()
        await second_task

        second_stt = created_stt[1]
        assert second_stt in hub.replace_peer_stt_calls
        assert hub.peer_stt is third_stt
        assert runtime.current_signature == third.runtime_signature
        assert sources["second-device"].close_calls == 1
        assert second_stt.close_backend_calls == 1
        assert second_stt.close_calls == 0
        assert sources["third-device"].close_calls == 0
        assert third_stt.close_backend_calls == 0
    finally:
        if "first-device" in sources:
            sources["first-device"].close_release.set()
        if runtime.state == PeerChannelRuntimeState.RUNNING:
            await runtime.close()
        await hub.close()


@pytest.mark.asyncio
async def test_disable_during_reconfigure_old_cleanup_detaches_old_peer_provider() -> None:
    hub = ProviderHandleBackedHub()
    created_stt: list[RetriableBackendClosingSTT] = []
    sources: dict[str, BlockingCloseSource] = {}

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = RetriableBackendClosingSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> BlockingCloseSource:
        source = BlockingCloseSource(
            name=config.output_device,
            block_on_close=config.output_device == "first-device",
        )
        sources[config.output_device] = source
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")
    reconfigure_task: asyncio.Task[None] | None = None

    await runtime.apply_policy(config=first, desired_active=True)
    first_stt = created_stt[0]
    try:
        reconfigure_task = asyncio.create_task(
            runtime.apply_policy(config=second, desired_active=True)
        )
        await sources["first-device"].close_started.wait()

        await runtime.apply_policy(config=second, desired_active=False)

        assert hub.peer_stt is None
        assert first_stt.close_backend_calls == 1

        sources["first-device"].close_release.set()
        await reconfigure_task

        second_stt = created_stt[1]
        assert second_stt in hub.replace_peer_stt_calls
        assert second_stt.close_backend_calls == 1
        assert second_stt.close_calls == 0
        assert hub.peer_stt is None
        assert runtime.state == PeerChannelRuntimeState.STOPPED
        assert runtime.current_signature is None
    finally:
        if "first-device" in sources:
            sources["first-device"].close_release.set()
        if reconfigure_task is not None:
            await asyncio.gather(reconfigure_task, return_exceptions=True)
        if runtime.state == PeerChannelRuntimeState.RUNNING:
            await runtime.close()
        await hub.close()


@pytest.mark.asyncio
async def test_disable_during_peer_replacement_before_runtime_adopts_detaches_replacement_without_events() -> (
    None
):
    hub = EventIngressProviderHandleBackedHub()
    created_stt: list[BlockingEventingSTT] = []

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = BlockingEventingSTT(
            name=config.output_device,
            block_backend_close=config.output_device == "first-device",
            close_backend_failures=1 if config.output_device == "first-device" else 0,
        )
        created_stt.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")
    reconfigure_task: asyncio.Task[None] | None = None
    disable_task: asyncio.Task[None] | None = None

    await runtime.apply_policy(config=first, desired_active=True)
    first_stt = created_stt[0]
    try:
        reconfigure_task = asyncio.create_task(
            runtime.apply_policy(config=second, desired_active=True)
        )
        await first_stt.close_backend_started.wait()
        second_stt = created_stt[1]

        assert hub.peer_stt is second_stt
        assert runtime._stt is first_stt

        second_stt.event_queue.put_nowait("replacement-event-before-adopt")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert hub.peer_events == []

        disable_task = asyncio.create_task(
            runtime.apply_policy(config=second, desired_active=False)
        )
        await asyncio.sleep(0)
        first_stt.close_backend_release.set()
        results = await asyncio.gather(reconfigure_task, disable_task, return_exceptions=True)

        assert any(isinstance(result, RuntimeError) for result in results)
        assert hub.peer_stt is None
        assert runtime.state == PeerChannelRuntimeState.STOPPED

        second_stt.event_queue.put_nowait("replacement-event-after-disable")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert hub.peer_events == []
    finally:
        first_stt.close_backend_release.set()
        tasks = [task for task in (reconfigure_task, disable_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()
        await hub.close()


@pytest.mark.asyncio
async def test_stale_peer_ingress_start_does_not_start_replacement_provider() -> None:
    old_stt = BlockingEventingSTT(name="old-peer")
    new_stt = BlockingEventingSTT(name="new-peer")
    hub = ClientHub(stt=None, peer_stt=old_stt, llm=None, osc=object())
    handle = hub.provider_runtime_handles["peer_stt"]
    hub._running = True

    await handle._lock.acquire()
    replace_task: asyncio.Task[object | None] | None = None
    stale_start_task: asyncio.Task[None] | None = None
    try:
        replace_task = asyncio.create_task(handle.replace_provider(new_stt, start=False))
        await asyncio.sleep(0)

        stale_start_task = asyncio.create_task(hub.start_peer_stt_provider_ingress(old_stt))
        await asyncio.sleep(0)
    finally:
        handle._lock.release()

    try:
        assert replace_task is not None
        assert stale_start_task is not None
        await asyncio.gather(replace_task, stale_start_task)

        assert hub.peer_stt is new_stt
        assert handle.event_task is None
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_disable_detaches_peer_provider_before_blocking_source_close() -> None:
    hub = ProviderHandleBackedHub()
    created_stt: list[RetriableBackendClosingSTT] = []
    sources: dict[str, BlockingCloseSource] = {}

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = RetriableBackendClosingSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> BlockingCloseSource:
        source = BlockingCloseSource(
            name=config.output_device,
            block_on_close=True,
        )
        sources[config.output_device] = source
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config(output_device="first-device")

    await runtime.apply_policy(config=config, desired_active=True)
    disable_task = asyncio.create_task(runtime.apply_policy(config=config, desired_active=False))
    try:
        await sources["first-device"].close_started.wait()

        assert hub.peer_stt is None
        assert hub.replace_peer_stt_calls[-1] is None
        assert created_stt[0].close_backend_calls == 1

        sources["first-device"].close_release.set()
        await disable_task
    finally:
        sources["first-device"].close_release.set()
        await asyncio.gather(disable_task, return_exceptions=True)
        await hub.close()


@pytest.mark.asyncio
async def test_reconfigure_attaches_peer_provider_before_blocking_old_source_close() -> None:
    hub = ProviderHandleBackedHub()
    created_stt: list[RetriableBackendClosingSTT] = []
    sources: dict[str, BlockingCloseSource] = {}

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = RetriableBackendClosingSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> BlockingCloseSource:
        source = BlockingCloseSource(
            name=config.output_device,
            block_on_close=config.output_device == "first-device",
        )
        sources[config.output_device] = source
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    await runtime.apply_policy(config=first, desired_active=True)
    reconfigure_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    try:
        await sources["first-device"].close_started.wait()

        assert hub.peer_stt is created_stt[1]
        assert hub.replace_peer_stt_calls[-1] is created_stt[1]

        sources["first-device"].close_release.set()
        await reconfigure_task
    finally:
        sources["first-device"].close_release.set()
        await asyncio.gather(reconfigure_task, return_exceptions=True)
        if runtime.state == PeerChannelRuntimeState.RUNNING:
            await runtime.close()
        await hub.close()


@pytest.mark.asyncio
async def test_stale_disable_teardown_does_not_overwrite_reenabled_generation_state() -> None:
    hub = DummyHub()
    created_stt: list[DummyManagedSTT] = []
    sources: dict[str, BlockingCloseSource] = {}

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = DummyManagedSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> BlockingCloseSource:
        source = BlockingCloseSource(
            name=config.output_device,
            block_on_close=config.output_device == "first-device",
        )
        sources[config.output_device] = source
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")
    disable_task: asyncio.Task[None] | None = None

    await runtime.apply_policy(config=first, desired_active=True)
    try:
        disable_task = asyncio.create_task(runtime.apply_policy(config=first, desired_active=False))
        await sources["first-device"].close_started.wait()

        await runtime.apply_policy(config=second, desired_active=True)
        second_stt = created_stt[1]

        assert runtime.state == PeerChannelRuntimeState.RUNNING
        assert runtime.current_signature == second.runtime_signature
        assert hub.peer_stt is second_stt

        sources["first-device"].close_release.set()
        await disable_task

        assert runtime.state == PeerChannelRuntimeState.RUNNING
        assert runtime.current_signature == second.runtime_signature
        assert hub.peer_stt is second_stt
        assert runtime._stt is second_stt
        assert runtime._audio_source is sources["second-device"]
        assert sources["second-device"].close_calls == 0
    finally:
        if "first-device" in sources:
            sources["first-device"].close_release.set()
        if disable_task is not None:
            await asyncio.gather(disable_task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_reconfigure_attaches_new_peer_provider_before_old_close_failure_surfaces() -> None:
    hub = ProviderHandleBackedHub()
    created_stt: list[RetriableBackendClosingSTT] = []
    sources: list[DummySource] = []

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = RetriableBackendClosingSTT(
            name=config.output_device,
            close_backend_failures=1 if config.output_device == "first-device" else 0,
        )
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> DummySource:
        _ = config
        source = DummySource()
        sources.append(source)
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    await runtime.apply_policy(config=first, desired_active=True)
    try:
        with pytest.raises(RuntimeError, match="first-device backend close failed"):
            await runtime.apply_policy(config=second, desired_active=True)

        assert hub.replace_peer_stt_calls == [created_stt[0], created_stt[1]]
        assert hub.peer_stt is created_stt[1]
        assert runtime.state == PeerChannelRuntimeState.RUNNING
        assert runtime.current_signature == second.runtime_signature
        assert runtime._stt is created_stt[1]
        assert runtime._audio_source is sources[1]
        assert runtime.loop_task is not None
        assert sources[0].close_calls == 1
        assert sources[1].close_calls == 0
        assert created_stt[1].close_backend_calls == 0
    finally:
        if runtime.state == PeerChannelRuntimeState.RUNNING:
            await runtime.close()
        await hub.close()


@pytest.mark.asyncio
async def test_reconfigure_attaches_replacement_after_old_source_close_failure() -> None:
    hub = DummyHub()
    created_stt: list[DummyManagedSTT] = []
    sources: list[DummySource] = []

    def stt_factory(config: PeerRuntimeConfig, on_terminal_failure):  # noqa: ANN001
        _ = on_terminal_failure
        stt = DummyManagedSTT(name=config.output_device)
        created_stt.append(stt)
        return stt

    def source_factory(config: PeerRuntimeConfig) -> DummySource:
        if config.output_device == "first-device":
            source = RetriableCloseSource(
                name="old source",
                close_failures=1,
            )
        else:
            source = DummySource()
        sources.append(source)
        return source

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=source_factory,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    await runtime.apply_policy(config=first, desired_active=True)
    try:
        with pytest.raises(RuntimeError, match="old source close failed"):
            await runtime.apply_policy(config=second, desired_active=True)

        assert hub.peer_stt is created_stt[1]
        assert runtime.state == PeerChannelRuntimeState.RUNNING
        assert runtime.current_signature == second.runtime_signature
        assert runtime._stt is created_stt[1]
        assert runtime._audio_source is sources[1]
        assert runtime.loop_task is not None
        assert sources[0].close_calls == 1
        assert sources[1].close_calls == 0
    finally:
        if runtime.state == PeerChannelRuntimeState.RUNNING:
            await runtime.close()


@pytest.mark.asyncio
async def test_warmup_does_not_interleave_with_reconfigure() -> None:
    hub = DummyHub()
    warmup_provider = BlockingWarmupSTT(name="first-device")
    created: list[BlockingWarmupSTT] = []
    reconfigure_started = asyncio.Event()

    def stt_factory(config, on_terminal_failure):
        _ = on_terminal_failure
        if config.output_device == "first-device":
            created.append(warmup_provider)
            return warmup_provider
        reconfigure_started.set()
        stt = BlockingWarmupSTT(name=config.output_device)
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    await runtime.apply_policy(config=first, desired_active=True)
    warmup_task = asyncio.create_task(runtime.warmup())
    await warmup_provider.warmup_started.wait()

    reconfigure_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    await asyncio.sleep(0)

    assert not reconfigure_started.is_set()

    warmup_provider.warmup_release.set()
    await warmup_task
    await reconfigure_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"


@pytest.mark.asyncio
async def test_warmup_during_running_state_does_not_build_a_second_peer_session() -> None:
    hub = DummyHub()
    stt = DummyManagedSTT()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: stt,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await runtime.warmup()
    await runtime.warmup()

    assert stt.warmup_calls == 2
    assert len(hub.replace_peer_stt_calls) == 1


@pytest.mark.asyncio
async def test_apply_policy_drops_superseded_in_flight_start_before_attach() -> None:
    hub = DummyHub()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def delayed_stt_factory(config: PeerRuntimeConfig, on_terminal_failure):
        _ = on_terminal_failure
        if config.output_device == "first-device":
            await first_release.wait()
        else:
            await second_release.wait()
        return DummyManagedSTT(name=config.output_device)

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=delayed_stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    first = make_peer_runtime_config(output_device="first-device")
    second = make_peer_runtime_config(output_device="second-device")

    first_task = asyncio.create_task(runtime.apply_policy(config=first, desired_active=True))
    second_task = asyncio.create_task(runtime.apply_policy(config=second, desired_active=True))
    second_release.set()
    await second_task
    first_release.set()
    await first_task

    assert hub.peer_stt is not None
    assert hub.peer_stt.name == "second-device"
    assert runtime.current_signature == second.runtime_signature


@pytest.mark.asyncio
async def test_source_open_failure_transitions_faulted_and_detaches() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: (_ for _ in ()).throw(RuntimeError("loopback open failed")),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_source_open_failure_backend_closes_unattached_peer_stt() -> None:
    hub = DummyHub()
    stt = DummyBackendClosingSTT()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: stt,
        source_factory=lambda config: (_ for _ in ()).throw(RuntimeError("loopback open failed")),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert stt.close_backend_calls == 1
    assert stt.close_calls == 0
    assert stt not in hub.replace_peer_stt_calls


@pytest.mark.asyncio
async def test_vad_startup_failure_closes_unattached_stt_after_source_close_failure() -> None:
    hub = DummyHub()
    stt = DummyBackendClosingSTT()
    source = FailingCloseSource(RuntimeError("source close failed"))
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: stt,
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: (_ for _ in ()).throw(
            RuntimeError("vad startup failed")
        ),
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    with pytest.raises(RuntimeError, match="source close failed"):
        await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert source.close_calls == 1
    assert stt.close_backend_calls == 1
    assert stt.close_calls == 0
    assert stt not in hub.replace_peer_stt_calls


@pytest.mark.asyncio
async def test_provider_factory_failure_transitions_faulted_without_attach() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: (_ for _ in ()).throw(
            RuntimeError("backend build failed")
        ),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls == []


@pytest.mark.asyncio
async def test_loop_crash_detaches_and_moves_runtime_to_faulted() -> None:
    hub = DummyHub()

    async def failing_run_audio_loop(**kwargs):
        _ = kwargs
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await asyncio.sleep(0)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_fault_teardown_invalidates_late_vad_sink_callbacks() -> None:
    hub = DummyHub()
    captured_sink: dict[str, object] = {}

    async def failing_run_audio_loop(**kwargs):
        captured_sink["sink"] = kwargs["sink"]
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await asyncio.sleep(0)

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert "sink" in captured_sink

    await captured_sink["sink"].handle_vad_event(object())  # type: ignore[attr-defined]

    assert hub.peer_events == []


@pytest.mark.asyncio
async def test_fault_teardown_close_failure_faults_detaches_and_observes_exception() -> None:
    observed_tasks: list[ExceptionObservingTask] = []
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()

    def task_factory(
        task_loop: asyncio.AbstractEventLoop,
        coro: Coroutine[Any, Any, None],
        **kwargs: object,
    ) -> ExceptionObservingTask:
        task = ExceptionObservingTask(coro, loop=task_loop, **kwargs)
        observed_tasks.append(task)
        return task

    loop.set_task_factory(task_factory)
    try:
        hub = DummyHub()
        source_failure = RuntimeError("source close failed")
        source = FailingCloseSource(source_failure)

        async def failing_run_audio_loop(**kwargs):  # noqa: ANN001
            _ = kwargs
            raise RuntimeError("loop crashed")

        runtime = PeerChannelRuntime(
            hub=hub,
            clock=FakeClock(),
            stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
            source_factory=lambda config: source,
            vad_factory=lambda config, model_path: "peer-vad",
            vad_model_resolver=lambda: Path("vad.onnx"),
            run_audio_loop=failing_run_audio_loop,
        )

        await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
        task = runtime.loop_task

        assert isinstance(task, ExceptionObservingTask)
        await wait_until(task.done)
        await asyncio.sleep(0)
        owner_exception_requests = task.exception_requests
    finally:
        loop.set_task_factory(previous_factory)
        for observed_task in observed_tasks:
            if observed_task.done() and observed_task.exception_requests == 0:
                _ = observed_task.exception()

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert source.close_calls == 1
    assert hub.peer_stt is None
    assert hub.replace_peer_stt_calls[-1] is None
    assert owner_exception_requests == 1


@pytest.mark.asyncio
async def test_fault_teardown_hub_detach_failure_still_faults_after_source_cleanup() -> None:
    detach_failure = RuntimeError("detach failed")
    hub = FailingDetachHub(detach_failure)
    source = DummySource()

    async def failing_run_audio_loop(**kwargs):  # noqa: ANN001
        _ = kwargs
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    task = runtime.loop_task

    assert task is not None
    await wait_until(task.done)
    await asyncio.sleep(0)
    _ = task.exception()

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert source.close_calls == 1
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_terminal_managed_stt_failure_auto_recovers_without_policy_reapply() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()

    await runtime.apply_policy(config=config, desired_active=True)
    await created[0].trigger_failure(RuntimeError("peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.RUNNING
    assert len(created) == 1
    assert hub.peer_stt is created[0]


@pytest.mark.asyncio
async def test_late_stt_failure_after_audio_loop_fault_does_not_recover() -> None:
    hub = DummyHub()
    created: list[FailureAwareSTT] = []

    def stt_factory(config, on_terminal_failure):
        _ = config
        stt = FailureAwareSTT()
        stt.on_terminal_failure = on_terminal_failure
        created.append(stt)
        return stt

    async def failing_run_audio_loop(**kwargs):
        _ = kwargs
        raise RuntimeError("loop crashed")

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=failing_run_audio_loop,
    )

    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    await asyncio.sleep(0)
    await created[0].trigger_failure(RuntimeError("late peer session closed"))

    assert runtime.state == PeerChannelRuntimeState.FAULTED
    assert len(created) == 1
    assert hub.peer_stt is None


@pytest.mark.asyncio
async def test_close_detaches_provider_and_cancels_running_loop() -> None:
    hub = DummyHub()
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    await runtime.close()

    assert hub.replace_peer_stt_calls[-1] is None
    assert runtime.state == PeerChannelRuntimeState.STOPPED


@pytest.mark.asyncio
async def test_close_source_failure_still_stops_invalidates_generation_and_detaches() -> None:
    hub = DummyHub()
    source = FailingCloseSource(RuntimeError("source close failed"))
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)
    running_generation = runtime._generation

    with pytest.raises(RuntimeError, match="source close failed"):
        await runtime.close()

    assert runtime.state == PeerChannelRuntimeState.STOPPED
    assert not runtime.is_current_generation(running_generation)
    assert source.close_calls == 1
    assert hub.peer_stt is None
    assert hub.replace_peer_stt_calls[-1] is None


@pytest.mark.asyncio
async def test_close_source_failure_retains_source_for_later_cleanup_retry() -> None:
    hub = DummyHub()
    source = RetriableCloseSource(name="peer source", close_failures=1)
    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=lambda config, on_terminal_failure: DummyManagedSTT(),
        source_factory=lambda config: source,
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    await runtime.apply_policy(config=make_peer_runtime_config(), desired_active=True)

    with pytest.raises(RuntimeError, match="peer source close failed"):
        await runtime.close()

    assert runtime.state == PeerChannelRuntimeState.STOPPED
    assert runtime._audio_source is None
    assert source.close_calls == 1

    await runtime.close()

    assert runtime.state == PeerChannelRuntimeState.STOPPED
    assert runtime._audio_source is None
    assert source.close_calls == 2


@pytest.mark.asyncio
async def test_discarded_unattached_stt_close_failure_retries_on_later_close() -> None:
    hub = DummyHub()
    stt = RetriableBackendClosingSTT(
        name="discarded peer",
        close_backend_failures=1,
    )
    stt_release = asyncio.Event()

    async def delayed_stt_factory(config, on_terminal_failure):  # noqa: ANN001
        _ = config, on_terminal_failure
        await stt_release.wait()
        return stt

    runtime = PeerChannelRuntime(
        hub=hub,
        clock=FakeClock(),
        stt_factory=delayed_stt_factory,
        source_factory=lambda config: DummySource(),
        vad_factory=lambda config, model_path: "peer-vad",
        vad_model_resolver=lambda: Path("vad.onnx"),
        run_audio_loop=fake_run_audio_loop,
    )
    config = make_peer_runtime_config()
    start_task = asyncio.create_task(runtime.apply_policy(config=config, desired_active=True))
    await asyncio.sleep(0)

    await runtime.apply_policy(config=config, desired_active=False)
    stt_release.set()

    with pytest.raises(RuntimeError, match="discarded peer backend close failed"):
        await start_task

    assert runtime.state == PeerChannelRuntimeState.STOPPED
    assert runtime._stt is None
    assert stt not in hub.replace_peer_stt_calls
    assert stt.close_backend_calls == 1

    await runtime.close()

    assert runtime.state == PeerChannelRuntimeState.STOPPED
    assert runtime._stt is None
    assert stt.close_backend_calls == 2
    assert stt.close_calls == 0
