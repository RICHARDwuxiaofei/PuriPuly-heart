from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from puripuly_heart.app.ports.runtime_resources import (
    ClearSelfSTTResult,
    STTProviderReadPort,
)
from puripuly_heart.config.resolved import ResolvedSTTConfig
from puripuly_heart.core.lifecycle import LifecycleScope, start_lifecycle_task
from puripuly_heart.core.runtime.local_qwen_lifecycle import LOCAL_QWEN_IDLE_RELEASE_SECONDS

SelfAudioStateChanged = Callable[["SelfAudioRuntime"], None]
SelfAudioErrorHandler = Callable[[Exception], None]


class SelfChannelState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class SelfChannelConfig:
    target_sample_rate_hz: int
    runtime_signature: tuple[object, ...]
    local_qwen: bool = False
    backend: ResolvedSTTConfig | None = None


@dataclass(frozen=True, slots=True)
class SetSelfSTTEnabled:
    enabled: bool
    config: SelfChannelConfig | None = None
    force_immediate: bool = False
    record_intent: bool = True


@dataclass(frozen=True, slots=True)
class SelfChannelSnapshot:
    desired_enabled: bool
    state: SelfChannelState
    provider_available: bool
    generation: int
    runtime_signature: tuple[object, ...] | None
    intent_generation: int = 0
    intent_enabled: bool = False


@dataclass(frozen=True, slots=True)
class SelfChannelCommandResult:
    status: Literal["applied", "provider_missing", "preparation_failed"]
    snapshot: SelfChannelSnapshot


@dataclass(frozen=True, slots=True)
class SelfIngressResume:
    config: SelfChannelConfig | None
    desired_enabled: bool
    generation: int
    intent_generation: int


class SelfSTTCommandPort(Protocol):
    async def execute(self, command: SetSelfSTTEnabled) -> SelfChannelCommandResult: ...


class SelfSTTStatePort(Protocol):
    def snapshot(self) -> SelfChannelSnapshot: ...


class SelfSTTProviderApplicationPort(Protocol):
    async def rebuild(self) -> object | None: ...


class SelfProviderHostPort(Protocol):
    async def clear_self_stt_for_toggle_off(self) -> ClearSelfSTTResult: ...

    async def drain_self_stt_for_toggle_off(
        self,
        *,
        release_backend_after: float | None = None,
    ) -> None: ...

    async def resume_self_stt_after_toggle_on(self) -> None: ...


class SelfVadIngressPort(Protocol):
    async def handle_self_vad_event(self, event: object, provider: object) -> None: ...


class SelfAudioLifecyclePort(Protocol):
    async def start_self_audio(self, config: SelfChannelConfig) -> None: ...

    async def stop_self_audio(self) -> None: ...


@dataclass(slots=True)
class _SelfVadSink:
    owner: "SelfSTTChannelOwner"
    generation: int

    async def handle_vad_event(self, event: object) -> None:
        await self.owner._handle_vad_event(event, self.generation)


class SelfSTTChannelOwner:
    def __init__(
        self,
        *,
        provider_read_port: STTProviderReadPort,
        provider_host: SelfProviderHostPort,
        ingress: SelfVadIngressPort,
        source_factory: Callable[[SelfChannelConfig], object],
        vad_factory: Callable[[SelfChannelConfig], object],
        run_audio_loop: Callable[..., Awaitable[None]],
        audio_lifecycle: SelfAudioLifecyclePort | None = None,
        audio_gate: object | None = None,
    ) -> None:
        self._provider_read_port = provider_read_port
        self._provider_host = provider_host
        self._ingress = ingress
        self._source_factory = source_factory
        self._vad_factory = vad_factory
        self._run_audio_loop = run_audio_loop
        self._audio_lifecycle = audio_lifecycle
        self._audio_gate = audio_gate
        self._source: object | None = None
        self._vad: object | None = None
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._intent_generation = 0
        self._intent_enabled = False
        self._desired = False
        self._state = SelfChannelState.STOPPED
        self._signature: tuple[object, ...] | None = None
        self._config: SelfChannelConfig | None = None
        self._lease = None
        self._lock = asyncio.Lock()
        self._scope = LifecycleScope("SelfSTTChannelOwner")

    def snapshot(self) -> SelfChannelSnapshot:
        lease = self._provider_read_port.lease_stt_provider("self_stt")
        return SelfChannelSnapshot(
            self._desired,
            self._state,
            lease is not None and lease.current is not None,
            self._generation,
            self._signature,
            self._intent_generation,
            self._intent_enabled,
        )

    def record_intent(self, enabled: bool) -> int:
        if enabled != self._intent_enabled:
            self._intent_generation += 1
            self._intent_enabled = enabled
        return self._intent_generation

    async def execute(self, command: SetSelfSTTEnabled) -> SelfChannelCommandResult:
        if command.record_intent:
            self.record_intent(command.enabled)
        if command.enabled and command.config is None:
            raise ValueError("self STT enable requires resolved channel config")
        if not command.enabled:
            await self._disable(
                clear_provider=True,
                force_immediate=command.force_immediate,
            )
            return SelfChannelCommandResult("applied", self.snapshot())
        assert command.config is not None
        if command.config.local_qwen:
            await self._provider_host.resume_self_stt_after_toggle_on()
        async with self._lock:
            lease = self._provider_read_port.lease_stt_provider("self_stt")
            if lease is None or lease.current is None:
                self._desired = False
                self._state = SelfChannelState.FAULTED
                return SelfChannelCommandResult("provider_missing", self.snapshot())
            if (
                self._desired
                and self._state is SelfChannelState.RUNNING
                and self._signature == command.config.runtime_signature
            ):
                return SelfChannelCommandResult("applied", self.snapshot())
            self._generation += 1
            generation = self._generation
            self._state = SelfChannelState.STARTING
            self._reset_audio_gate()
            if self._audio_lifecycle is not None:
                try:
                    await self._audio_lifecycle.stop_self_audio()
                    await self._audio_lifecycle.start_self_audio(command.config)
                except Exception:
                    self._desired = False
                    self._state = SelfChannelState.FAULTED
                    return SelfChannelCommandResult("preparation_failed", self.snapshot())
                self._lease = lease
                self._desired = True
                self._signature = command.config.runtime_signature
                self._config = command.config
                self._state = SelfChannelState.RUNNING
                source = None
                vad = None
            else:
                source = None
                try:
                    source = self._source_factory(command.config)
                    vad = self._vad_factory(command.config)
                except Exception:
                    await self._close_source(source)
                    self._desired = False
                    self._state = SelfChannelState.FAULTED
                    return SelfChannelCommandResult("preparation_failed", self.snapshot())
            if lease.current is None:
                await self._close_source(source)
                self._desired = False
                self._state = SelfChannelState.FAULTED
                return SelfChannelCommandResult("provider_missing", self.snapshot())
            if self._audio_lifecycle is None:
                await self._stop_ingress_locked()
                self._source = source
                self._vad = vad
                self._lease = lease
                self._desired = True
                self._signature = command.config.runtime_signature
                self._config = command.config
                self._task = start_lifecycle_task(
                    self._scope,
                    self._run_guarded(command.config, generation),
                    name="audio-loop",
                )
                self._state = SelfChannelState.RUNNING
        provider = lease.current
        warmup = getattr(provider, "warmup", None)
        if not command.config.local_qwen and callable(warmup):
            result = warmup()
            if inspect.isawaitable(result):
                await result
        return SelfChannelCommandResult("applied", self.snapshot())

    async def close(self) -> None:
        await self._disable(clear_provider=False, force_immediate=True)
        await self._scope.close()

    async def freeze_for_provider_replacement(self) -> SelfIngressResume:
        async with self._lock:
            resume = SelfIngressResume(
                self._config,
                self._desired,
                self._generation + 1,
                self._intent_generation,
            )
            self._generation += 1
            self._desired = False
            self._reset_audio_gate()
            await self._stop_ingress_locked()
            self._state = SelfChannelState.STOPPED
            self._lease = None
        return resume

    async def resume_after_provider_replacement(self, resume: SelfIngressResume) -> None:
        async with self._lock:
            still_current = (
                self._intent_generation == resume.intent_generation
                and self._intent_enabled == resume.desired_enabled
                and self._generation == resume.generation
                and not self._desired
            )
        if still_current and resume.config is not None and resume.desired_enabled:
            await self.execute(SetSelfSTTEnabled(True, resume.config, record_intent=False))

    async def _disable(self, *, clear_provider: bool, force_immediate: bool) -> None:
        async with self._lock:
            config = self._config
            self._generation += 1
            self._desired = False
            self._reset_audio_gate()
            if self._audio_lifecycle is None:
                await self._stop_ingress_locked()
            else:
                await self._audio_lifecycle.stop_self_audio()
            if clear_provider:
                if config is not None and config.local_qwen and not force_immediate:
                    await self._provider_host.drain_self_stt_for_toggle_off(
                        release_backend_after=LOCAL_QWEN_IDLE_RELEASE_SECONDS
                    )
                else:
                    await self._provider_host.clear_self_stt_for_toggle_off()
            self._state = SelfChannelState.STOPPED
            self._signature = None
            self._config = None
            self._lease = None

    async def _stop_ingress_locked(self) -> None:
        task = self._task
        self._task = None
        source = self._source
        self._source = None
        self._vad = None
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_source(source)

    async def _run_guarded(self, config: SelfChannelConfig, generation: int) -> None:
        try:
            await self._run_audio_loop(
                source=self._source,
                vad=self._vad,
                sink=_SelfVadSink(self, generation),
                target_sample_rate_hz=config.target_sample_rate_hz,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                if generation == self._generation:
                    self._state = SelfChannelState.FAULTED
                    self._desired = False

    async def _handle_vad_event(self, event: object, generation: int) -> None:
        if generation != self._generation or not self._desired:
            return
        lease = self._lease
        provider = None if lease is None else lease.current
        if provider is None:
            return
        await self._ingress.handle_self_vad_event(event, provider)
        if generation != self._generation or lease.current is None:
            return

    @staticmethod
    async def _close_source(source: object | None) -> None:
        close = getattr(source, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    def _reset_audio_gate(self) -> None:
        reset = getattr(self._audio_gate, "reset", None)
        if callable(reset):
            reset()


@dataclass(slots=True)
class _GenerationGuardedVadSink:
    sink: object
    runtime: "SelfAudioRuntime"
    generation: int

    def __getattr__(self, name: str) -> object:
        return getattr(self.sink, name)

    async def handle_vad_event(self, event: object) -> None:
        if not self.runtime.is_current_generation(self.generation):
            return
        await self.sink.handle_vad_event(event)  # type: ignore[attr-defined]


class SelfAudioRuntime:
    """Owns the self microphone source, VAD object, and VAD loop task."""

    resource_fields = (
        "_audio_source",
        "_vad",
        "_loop_task",
        "_generation",
        "_last_close_exception",
    )
    stop_ingress = "stop microphone audio producer and invalidate VAD generation"
    toggle_off_policy = "toggle-off immediately closes STT, then cancels/closes the mic loop"
    shutdown_policy = "app shutdown cancels mic loop and closes microphone source without STT drain"
    late_callback_rule = "generation guard rejects late self VAD callbacks"

    def __init__(
        self,
        *,
        state_changed: SelfAudioStateChanged | None = None,
        error_handler: SelfAudioErrorHandler | None = None,
    ) -> None:
        self._state_changed = state_changed
        self._error_handler = error_handler
        self._audio_source: object | None = None
        self._vad: object | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._last_close_exception: BaseException | None = None

    @property
    def audio_source(self) -> object | None:
        return self._audio_source

    @property
    def vad(self) -> object | None:
        return self._vad

    @property
    def loop_task(self) -> asyncio.Task[None] | None:
        return self._loop_task

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_close_exception(self) -> BaseException | None:
        return self._last_close_exception

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": "SelfAudioRuntime",
            "resource_fields": self.resource_fields,
            "stop_ingress": self.stop_ingress,
            "toggle_off_policy": self.toggle_off_policy,
            "shutdown_policy": self.shutdown_policy,
            "late_callback_rule": self.late_callback_rule,
        }

    def adopt_legacy_state(
        self,
        *,
        task: asyncio.Task[None] | None,
        source: object | None,
        vad: object | None,
        last_close_exception: BaseException | None,
    ) -> None:
        self._loop_task = task
        self._audio_source = source
        self._vad = vad
        self._last_close_exception = last_close_exception
        self._notify_state_changed()

    def start(
        self,
        *,
        source: object,
        vad: object,
        run_loop: Callable[[], Awaitable[None]],
    ) -> None:
        if self._loop_task is not None or self._audio_source is not None or self._vad is not None:
            return
        self._generation += 1
        self._audio_source = source
        self._vad = vad
        self._last_close_exception = None
        self._notify_state_changed()
        generation = self._generation
        self._loop_task = asyncio.create_task(
            self._run_loop_guarded(run_loop=run_loop, generation=generation),
            name="SelfAudioRuntime:mic-loop",
        )
        self._loop_task.add_done_callback(self._on_loop_task_done)
        self._notify_state_changed()

    def guard_vad_sink(self, sink: object) -> object:
        return _GenerationGuardedVadSink(
            sink=sink,
            runtime=self,
            generation=self._generation,
        )

    def is_current_generation(self, generation: int) -> bool:
        return generation == self._generation and self._loop_task is not None

    async def stop(self) -> None:
        self._generation += 1
        task = self._loop_task
        self._loop_task = None
        self._notify_state_changed()
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        source = self._audio_source
        close_failure: Exception | None = None
        if source is not None:
            try:
                close = getattr(source, "close", None)
                if callable(close):
                    await close()
            except Exception as exc:
                self._last_close_exception = exc
                close_failure = exc
            else:
                self._last_close_exception = None
                self._audio_source = None

        self._vad = None
        self._notify_state_changed()
        if close_failure is not None:
            raise close_failure

    async def close(self) -> None:
        await self.stop()

    async def _run_loop_guarded(
        self,
        *,
        run_loop: Callable[[], Awaitable[None]],
        generation: int,
    ) -> None:
        try:
            if self.is_current_generation(generation):
                await run_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._error_handler is not None:
                self._error_handler(exc)
            raise

    def _on_loop_task_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
        if self._loop_task is task:
            self._loop_task = None
            self._generation += 1
            self._notify_state_changed()

    def _notify_state_changed(self) -> None:
        if self._state_changed is not None:
            self._state_changed(self)


__all__ = [
    "SelfAudioRuntime",
    "SelfChannelCommandResult",
    "SelfChannelConfig",
    "SelfChannelSnapshot",
    "SelfChannelState",
    "SelfSTTChannelOwner",
    "SelfSTTCommandPort",
    "SelfSTTProviderApplicationPort",
    "SelfSTTStatePort",
    "SetSelfSTTEnabled",
]
