from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

SelfAudioStateChanged = Callable[["SelfAudioRuntime"], None]
SelfAudioErrorHandler = Callable[[Exception], None]


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
    toggle_off_policy = "toggle-off drains STT separately, then cancels/closes the mic loop"
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


__all__ = ["SelfAudioRuntime"]
