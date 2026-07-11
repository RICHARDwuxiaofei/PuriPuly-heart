from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Protocol

from puripuly_heart.config.resolved import ResolvedSTTConfig
from puripuly_heart.core.clock import Clock

if TYPE_CHECKING:
    from puripuly_heart.core.orchestrator.hub import ClientHub


class PeerChannelRuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class PeerRuntimeConfig:
    backend: ResolvedSTTConfig
    output_device: str
    vad_threshold: float
    vad_hangover_ms: int
    vad_pre_roll_ms: int
    provider_signature: tuple[object, ...]
    runtime_signature: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class PeerIngressResume:
    config: PeerRuntimeConfig | None
    desired_active: bool
    frozen_generation: int
    intent_generation: int


@dataclass(frozen=True, slots=True)
class PeerPolicySnapshot:
    generation: int
    desired_active: bool
    intent_generation: int
    intent_desired_active: bool


class SpeechChannelRuntime(Protocol):
    @property
    def state(self) -> PeerChannelRuntimeState: ...

    @property
    def current_signature(self) -> object | None: ...

    async def apply_policy(self, *, config: PeerRuntimeConfig, desired_active: bool) -> None: ...
    async def warmup(self) -> None: ...
    async def close(self) -> None: ...


class _PeerSTTLease(Protocol):
    slot: Literal["self_stt", "peer_stt"]
    identity: str
    generation: int

    @property
    def current(self) -> object | None: ...

    @property
    def is_current(self) -> bool: ...


class PeerSTTReadPort(Protocol):
    def lease_stt_provider(self, slot: Literal["self_stt", "peer_stt"]) -> _PeerSTTLease | None: ...


@dataclass(slots=True)
class _PeerHubVadSink:
    runtime: "PeerChannelRuntime"
    generation: int

    async def handle_vad_event(self, event) -> None:  # noqa: ANN001
        await self.runtime._handle_vad_event(event, generation=self.generation)


class PeerChannelRuntime:
    resource_fields = (
        "_audio_source",
        "_vad",
        "_loop_task",
        "_generation",
        "_desired_active",
        "_lock",
    )
    stop_ingress = "invalidate generation and desired-active state"
    shutdown_policy = "cancel loop and close peer audio source"
    late_callback_rule = "late peer callbacks cannot mutate current runtime or output to chatbox"

    def __init__(
        self,
        *,
        hub: ClientHub,
        clock: Clock,
        provider_read_port: PeerSTTReadPort,
        source_factory: Callable[[PeerRuntimeConfig], object],
        vad_factory: Callable[[PeerRuntimeConfig, Path], object],
        vad_model_resolver: Callable[[], Path],
        run_audio_loop: Callable[..., Awaitable[None]],
    ) -> None:
        self.hub = hub
        self.clock = clock
        self._provider_read_port = provider_read_port
        self._source_factory = source_factory
        self._vad_factory = vad_factory
        self._vad_model_resolver = vad_model_resolver
        self._run_audio_loop = run_audio_loop
        self._config: PeerRuntimeConfig | None = None
        self._audio_source: object | None = None
        self._vad: object | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._signature: tuple[object, ...] | None = None
        self._state = PeerChannelRuntimeState.STOPPED
        self._generation = 0
        self._desired_active = False
        self._intent_generation = 0
        self._intent_desired_active = False
        self._lock = asyncio.Lock()
        self._retired_sources: list[object] = []
        self._source_close_lock = asyncio.Lock()
        self._closed_source_ids: set[int] = set()

    @property
    def state(self) -> PeerChannelRuntimeState:
        return self._state

    @property
    def current_signature(self) -> object | None:
        return self._signature

    def policy_snapshot(self) -> PeerPolicySnapshot:
        return PeerPolicySnapshot(
            self._generation,
            self._desired_active,
            self._intent_generation,
            self._intent_desired_active,
        )

    @property
    def loop_task(self) -> asyncio.Task[None] | None:
        return self._loop_task

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": "PeerChannelRuntime",
            "resource_fields": self.resource_fields,
            "stop_ingress": self.stop_ingress,
            "shutdown_policy": self.shutdown_policy,
            "late_callback_rule": self.late_callback_rule,
        }

    async def apply_policy(
        self,
        *,
        config: PeerRuntimeConfig,
        desired_active: bool,
        record_intent: bool = True,
    ) -> None:
        async with self._lock:
            if record_intent and desired_active != self._intent_desired_active:
                self._intent_generation += 1
                self._intent_desired_active = desired_active
            if (
                desired_active
                and self._desired_active
                and self._state == PeerChannelRuntimeState.RUNNING
                and self._signature == config.runtime_signature
            ):
                self._config = config
                return
            self._generation += 1
            generation = self._generation
            self._config = config
            self._desired_active = desired_active
            self._state = (
                PeerChannelRuntimeState.STARTING
                if desired_active
                else PeerChannelRuntimeState.STOPPING
            )
        if desired_active:
            await self._start_generation(generation, config)
        else:
            await self._teardown_resources(PeerChannelRuntimeState.STOPPED, generation)

    async def warmup(self) -> None:
        async with self._lock:
            should_warm = self._desired_active and self._state == PeerChannelRuntimeState.RUNNING
        if not should_warm:
            return
        lease = self._provider_read_port.lease_stt_provider("peer_stt")
        provider = None if lease is None else lease.current
        warmup = getattr(provider, "warmup", None)
        if not callable(warmup):
            return
        result = warmup()
        if inspect.isawaitable(result):
            await result
        if lease is not None:
            _ = lease.is_current

    async def freeze_for_provider_replacement(self) -> PeerIngressResume:
        async with self._lock:
            config = self._config
            desired_active = self._desired_active
        if config is not None:
            await self.apply_policy(config=config, desired_active=False, record_intent=False)
        async with self._lock:
            return PeerIngressResume(
                config,
                desired_active,
                self._generation,
                self._intent_generation,
            )

    async def resume_after_provider_replacement(self, resume: PeerIngressResume) -> None:
        async with self._lock:
            still_current = (
                self._intent_generation == resume.intent_generation
                and self._intent_desired_active == resume.desired_active
                and not self._desired_active
            )
        if still_current and resume.config is not None and resume.desired_active:
            await self.apply_policy(
                config=resume.config,
                desired_active=True,
                record_intent=False,
            )

    async def close(self) -> None:
        async with self._lock:
            self._generation += 1
            generation = self._generation
            self._desired_active = False
            self._state = PeerChannelRuntimeState.STOPPING
        await self._teardown_resources(PeerChannelRuntimeState.STOPPED, generation)

    async def _start_generation(self, generation: int, config: PeerRuntimeConfig) -> None:
        lease = self._provider_read_port.lease_stt_provider("peer_stt")
        if lease is None or lease.current is None:
            await self._mark_faulted_if_current(generation)
            return
        source = None
        try:
            source = self._source_factory(config)
            vad = self._vad_factory(config, self._vad_model_resolver())
        except Exception:
            await self._close_or_retain_source(source)
            await self._mark_faulted_if_current(generation)
            return
        if self._is_superseded(generation):
            await self._close_or_retain_source(source)
            return
        async with self._lock:
            if self._is_superseded(generation):
                install = False
                old_loop = None
                old_source = None
            else:
                install = True
                old_loop = self._loop_task
                old_source = self._audio_source
                self._loop_task = None
                self._audio_source = source
                self._vad = vad
                self._signature = config.runtime_signature
        if not install:
            await self._close_or_retain_source(source)
            return
        await self._cancel_loop(old_loop)
        await self._close_or_retain_source(old_source)
        async with self._lock:
            if self._is_superseded(generation) or self._audio_source is not source:
                start_loop = False
            else:
                start_loop = True
                task = asyncio.create_task(
                    self._run_peer_loop_guarded(
                        source=source,
                        vad=vad,
                        target_sample_rate_hz=config.backend.sample_rate_hz,
                        generation=generation,
                    )
                )
                task.add_done_callback(self._on_loop_task_done)
                self._loop_task = task
                self._state = PeerChannelRuntimeState.RUNNING
        if not start_loop:
            await self._close_or_retain_source(source)

    async def _handle_vad_event(self, event: object, *, generation: int) -> None:
        if self._is_superseded(generation):
            return
        lease = self._provider_read_port.lease_stt_provider("peer_stt")
        provider = None if lease is None else lease.current
        if provider is None:
            return
        await self.hub.handle_peer_vad_event(event, stt_provider=provider)
        if lease is None or not lease.is_current or self._is_superseded(generation):
            return

    async def _run_peer_loop_guarded(self, **kwargs: object) -> None:
        generation = int(kwargs.pop("generation"))
        try:
            await self._run_audio_loop(
                **kwargs,
                sink=_PeerHubVadSink(runtime=self, generation=generation),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._mark_faulted_if_current(generation)

    async def _mark_faulted_if_current(self, generation: int) -> None:
        async with self._lock:
            if self._is_superseded(generation):
                return
            self._generation += 1
            teardown_generation = self._generation
            self._desired_active = False
        await self._teardown_resources(PeerChannelRuntimeState.FAULTED, teardown_generation)

    async def _teardown_resources(
        self, target_state: PeerChannelRuntimeState, generation: int
    ) -> None:
        async with self._lock:
            task = self._loop_task
            source = self._audio_source
            self._loop_task = None
            self._audio_source = None
            self._vad = None
            self._signature = None
        retired_before = tuple(self._retired_sources)
        failures: list[Exception] = []
        try:
            await self._cancel_loop(task)
        except Exception as exc:
            failures.append(exc)
        try:
            await self._close_or_retain_source(source)
        except Exception as exc:
            failures.append(exc)
        for retired in retired_before:
            try:
                await self._close_if_possible(retired)
            except Exception as exc:
                failures.append(exc)
            else:
                self._retired_sources.remove(retired)
        async with self._lock:
            if self._generation == generation:
                self._state = target_state
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup("peer channel teardown failed", failures)

    async def _close_or_retain_source(self, source: object | None) -> None:
        if source is None:
            return
        try:
            await self._close_if_possible(source)
        except Exception:
            if not any(item is source for item in self._retired_sources):
                self._retired_sources.append(source)
            raise

    async def _cancel_loop(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _on_loop_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def _close_if_possible(self, resource: object | None) -> None:
        identity = id(resource)
        async with self._source_close_lock:
            if identity in self._closed_source_ids:
                return
            await self._close_resource(resource)
            self._closed_source_ids.add(identity)

    @staticmethod
    async def _close_resource(resource: object | None) -> None:
        close = getattr(resource, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def _is_superseded(self, generation: int) -> bool:
        return generation != self._generation or not self._desired_active

    def is_current_generation(self, generation: int) -> bool:
        return not self._is_superseded(generation)
