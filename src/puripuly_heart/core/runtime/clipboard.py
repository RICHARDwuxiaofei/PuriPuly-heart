from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

ClipboardWatcherFactory = Callable[[Callable[[str], None]], object]
ClipboardSubmitHandler = Callable[[str], Awaitable[None]]
ClipboardRuntimeStateChanged = Callable[["ClipboardRuntime"], None]


class ClipboardRuntime:
    """Owns clipboard watcher ingress and clipboard submit tasks."""

    resource_fields = (
        "_watcher",
        "_loop",
        "_generation",
        "_submit_tasks",
    )
    stop_ingress = "stop watcher before runtime shutdown"
    shutdown_policy = "join watcher; gather submit tasks with bounded timeout"
    late_callback_rule = "generation guard rejects late clipboard callbacks"

    def __init__(
        self,
        *,
        watcher_factory: ClipboardWatcherFactory,
        submit_handler: ClipboardSubmitHandler,
        submit_gather_timeout_s: float = 2.0,
        state_changed: ClipboardRuntimeStateChanged | None = None,
    ) -> None:
        self._watcher_factory = watcher_factory
        self._submit_handler = submit_handler
        self._submit_gather_timeout_s = max(0.0, float(submit_gather_timeout_s))
        self._state_changed = state_changed
        self._watcher: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generation = 0
        self._submit_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._closed = False
        self._lock = asyncio.Lock()
        self._submit_gather_timeouts = 0

    @property
    def owner_name(self) -> str:
        return "ClipboardRuntime"

    @property
    def watcher(self) -> object | None:
        return self._watcher

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active_submit_task_count(self) -> int:
        return len(self._submit_tasks)

    @property
    def submit_gather_timeouts(self) -> int:
        return self._submit_gather_timeouts

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": self.owner_name,
            "resource_fields": self.resource_fields,
            "stop_ingress": self.stop_ingress,
            "shutdown_policy": self.shutdown_policy,
            "late_callback_rule": self.late_callback_rule,
        }

    def adopt_legacy_state(
        self,
        *,
        watcher: object | None,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        self._generation += 1
        self._watcher = watcher
        self._loop = loop
        self._closed = False
        self._closing = False
        self._notify_state_changed()

    async def sync(self, *, enabled: bool, strict_runtime_errors: bool = False) -> None:
        if not enabled:
            await self.stop(strict_runtime_errors=strict_runtime_errors)
            return
        await self.start(strict_runtime_errors=strict_runtime_errors)

    async def start(self, *, strict_runtime_errors: bool = False) -> None:
        async with self._lock:
            if self._watcher is not None:
                return
            if self._closing or self._closed:
                if strict_runtime_errors:
                    raise RuntimeError("ClipboardRuntime is closed to new watcher work")
                return
            self._generation += 1
            generation = self._generation
            self._loop = asyncio.get_running_loop()
            watcher = self._watcher_factory(
                lambda text, *, _generation=generation: self.on_text_from_thread(
                    text,
                    generation=_generation,
                )
            )
            try:
                await asyncio.to_thread(watcher.start)  # type: ignore[attr-defined]
            except Exception:
                self._loop = None
                self._notify_state_changed()
                try:
                    await asyncio.to_thread(watcher.stop)  # type: ignore[attr-defined]
                except Exception:
                    pass
                if strict_runtime_errors:
                    raise
                return
            if generation != self._generation or self._closing or self._closed:
                await asyncio.to_thread(watcher.stop)  # type: ignore[attr-defined]
                return
            self._watcher = watcher
            self._notify_state_changed()

    async def stop(self, *, strict_runtime_errors: bool = False) -> None:
        async with self._lock:
            self._generation += 1
            watcher = self._watcher
            self._watcher = None
            self._loop = None
            self._notify_state_changed()
        stop_failure: Exception | None = None
        if watcher is not None:
            try:
                await asyncio.to_thread(watcher.stop)  # type: ignore[attr-defined]
            except Exception as exc:
                stop_failure = exc
        await self._gather_submit_tasks_bounded()
        self._notify_state_changed()
        if stop_failure is not None and strict_runtime_errors:
            raise stop_failure

    async def close(self) -> None:
        if self._closed and self._watcher is None and not self._submit_tasks:
            return
        self._closing = True
        try:
            await self.stop(strict_runtime_errors=True)
        finally:
            self._closed = True
            self._closing = False

    def on_text_from_thread(self, text: str, *, generation: int | None = None) -> None:
        trimmed = text.strip()
        if not trimmed or len(trimmed) > 300:
            return
        expected_generation = self._generation if generation is None else generation
        if not self._is_current_generation(expected_generation):
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self.submit_from_loop,
            trimmed,
            expected_generation,
        )

    def submit_from_loop(self, text: str, generation: int | None = None) -> None:
        expected_generation = self._generation if generation is None else generation
        if not self._is_current_generation(expected_generation):
            return
        task = asyncio.create_task(
            self._run_submit(text, expected_generation),
            name="ClipboardRuntime:submit",
        )
        self._submit_tasks.add(task)
        task.add_done_callback(self._on_submit_done)
        self._notify_state_changed()

    def _is_current_generation(self, generation: int) -> bool:
        return not self._closing and not self._closed and generation == self._generation

    async def _run_submit(self, text: str, generation: int) -> None:
        if not self._is_current_generation(generation):
            return
        await self._submit_handler(text)

    async def _gather_submit_tasks_bounded(self) -> None:
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._submit_tasks if task is not current_task)
        if not tasks:
            return

        done, pending = await asyncio.wait(tasks, timeout=self._submit_gather_timeout_s)
        for task in done:
            self._observe_submit_task_exception(task)
        if pending:
            self._submit_gather_timeouts += 1
            for task in pending:
                task.cancel()
            done_after_cancel, _still_pending = await asyncio.wait(
                pending,
                timeout=self._submit_gather_timeout_s,
            )
            for task in done_after_cancel:
                self._observe_submit_task_exception(task)
        for task in tasks:
            self._submit_tasks.discard(task)

    def _on_submit_done(self, task: asyncio.Task[Any]) -> None:
        self._submit_tasks.discard(task)
        self._observe_submit_task_exception(task)
        self._notify_state_changed()

    @staticmethod
    def _observe_submit_task_exception(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            try:
                task.exception()
            except asyncio.CancelledError:
                pass

    def _notify_state_changed(self) -> None:
        if self._state_changed is not None:
            self._state_changed(self)


__all__ = ["ClipboardRuntime"]
