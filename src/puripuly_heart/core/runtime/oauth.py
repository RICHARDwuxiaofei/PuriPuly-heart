from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

_TaskResultT = TypeVar("_TaskResultT")
ExternalTaskRunner = Callable[[Callable[[], Awaitable[Any]]], object]


class OAuthRuntime:
    """Owns OAuth loopback listeners and auth-related background tasks."""

    resource_fields = (
        "_auth_tasks",
        "_external_task_handles",
        "_loopback_listeners",
    )
    stop_ingress = "cancel auth task / close listener"
    shutdown_policy = "listener close unblocks wait; browser side effects are not retried"
    late_callback_rule = (
        "second close is a no-op; late callback maps to cancelled or expired result"
    )

    def __init__(self, *, auth_task_timeout_s: float = 2.0) -> None:
        self._auth_task_timeout_s = max(0.0, float(auth_task_timeout_s))
        self._auth_tasks: dict[str, asyncio.Task[Any]] = {}
        self._task_names: dict[asyncio.Task[Any], str] = {}
        self._external_task_handles: dict[str, object] = {}
        self._loopback_listeners: dict[str, object] = {}
        self._closing = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def owner_name(self) -> str:
        return "OAuthRuntime"

    @property
    def active_task_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._auth_tasks))

    @property
    def external_task_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._external_task_handles))

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def active_listener_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._loopback_listeners))

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": self.owner_name,
            "resource_fields": self.resource_fields,
            "stop_ingress": self.stop_ingress,
            "shutdown_policy": self.shutdown_policy,
            "late_callback_rule": self.late_callback_rule,
        }

    def create_auth_task(
        self,
        coroutine: Coroutine[Any, Any, _TaskResultT],
        *,
        task_name: str,
    ) -> asyncio.Task[_TaskResultT]:
        if self._closing or self._closed:
            coroutine.close()
            state = "closing" if self._closing else "closed"
            raise RuntimeError(f"OAuthRuntime is {state} to new auth tasks")
        existing = self._auth_tasks.get(task_name)
        if existing is not None and not existing.done():
            coroutine.close()
            raise RuntimeError(f"OAuthRuntime already owns auth task {task_name!r}")

        task = asyncio.create_task(coroutine, name=f"{self.owner_name}:{task_name}")
        self._auth_tasks[task_name] = task
        self._task_names[task] = task_name
        task.add_done_callback(self._on_auth_task_done)
        return task

    def attach_loopback_listener(self, listener: object, *, listener_name: str) -> None:
        if self._closing or self._closed:
            raise RuntimeError("OAuthRuntime is closed to new loopback listeners")
        self._loopback_listeners[listener_name] = listener

    async def close_loopback_listener(self, listener: object, *, listener_name: str) -> None:
        current = self._loopback_listeners.get(listener_name)
        await _close_listener(listener)
        if current is listener:
            self._loopback_listeners.pop(listener_name, None)

    def detach_loopback_listener(self, listener: object, *, listener_name: str) -> None:
        current = self._loopback_listeners.get(listener_name)
        if current is listener:
            self._loopback_listeners.pop(listener_name, None)

    def start_external_task(
        self,
        *,
        task_runner: ExternalTaskRunner,
        task_factory: Callable[[], Awaitable[Any]],
        task_name: str,
        generation: int | None = None,
    ) -> object:
        _ = generation
        if self._closing or self._closed:
            raise RuntimeError("OAuthRuntime is closed to new external auth tasks")
        if task_name in self._external_task_handles:
            existing = self._external_task_handles[task_name]
            done = getattr(existing, "done", None)
            if not (callable(done) and bool(done())):
                raise RuntimeError(f"OAuthRuntime already owns external task {task_name!r}")
            self._external_task_handles.pop(task_name, None)
        handle = task_runner(task_factory)
        self._external_task_handles[task_name] = handle
        add_done_callback = getattr(handle, "add_done_callback", None)
        if callable(add_done_callback):
            add_done_callback(
                lambda finished, *, _task_name=task_name: self._clear_external_task(
                    _task_name,
                    finished,
                )
            )
        return handle

    def cancel_external_task(
        self,
        handle: object | None = None,
        *,
        task_name: str | None = None,
    ) -> None:
        if task_name is None:
            for name, candidate in tuple(self._external_task_handles.items()):
                if candidate is handle:
                    task_name = name
                    break
        if task_name is not None:
            handle = self._external_task_handles.pop(task_name, handle)
        cancel = getattr(handle, "cancel", None)
        if callable(cancel):
            cancel()

    def clear_external_task(self, task_name: str, handle: object | None = None) -> None:
        current = self._external_task_handles.get(task_name)
        if handle is None or current is handle:
            self._external_task_handles.pop(task_name, None)

    async def close(self) -> None:
        if self._closed and not self._has_resources():
            return

        async with self._close_lock:
            if self._closed and not self._has_resources():
                return
            self._closing = True
            self._closed = True
            failures: list[Exception] = []
            try:
                failures.extend(await self._close_loopback_listeners())
                failures.extend(self._cancel_external_tasks())
                failures.extend(await self._cancel_and_gather_auth_tasks())
            finally:
                self._closing = False
            _raise_cleanup_failures("OAuthRuntime close failed", failures)

    def _has_resources(self) -> bool:
        return bool(self._auth_tasks or self._external_task_handles or self._loopback_listeners)

    async def _close_loopback_listeners(self) -> list[Exception]:
        failures: list[Exception] = []
        for listener_name, listener in tuple(self._loopback_listeners.items()):
            try:
                await _close_listener(listener)
            except Exception as exc:
                failures.append(exc)
            else:
                if self._loopback_listeners.get(listener_name) is listener:
                    self._loopback_listeners.pop(listener_name, None)
        return failures

    def _cancel_external_tasks(self) -> list[Exception]:
        failures: list[Exception] = []
        handles = tuple(self._external_task_handles.values())
        self._external_task_handles.clear()
        for handle in handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    failures.append(exc)
        return failures

    async def _cancel_and_gather_auth_tasks(self) -> list[Exception]:
        failures: list[Exception] = []
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._task_names if task is not current_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._auth_task_timeout_s)
            for task in done:
                _observe_task_exception(task)
            if pending:
                for task in pending:
                    task.cancel()
                done_after_cancel, _still_pending = await asyncio.wait(
                    pending,
                    timeout=self._auth_task_timeout_s,
                )
                for task in done_after_cancel:
                    _observe_task_exception(task)
        for task in tasks:
            task_name = self._task_names.pop(task, None)
            if task_name is not None:
                self._auth_tasks.pop(task_name, None)
        return failures

    def _on_auth_task_done(self, task: asyncio.Task[Any]) -> None:
        task_name = self._task_names.pop(task, None)
        if task_name is not None and self._auth_tasks.get(task_name) is task:
            self._auth_tasks.pop(task_name, None)
        _observe_task_exception(task)

    def _clear_external_task(self, task_name: str, handle: object) -> None:
        if self._external_task_handles.get(task_name) is handle:
            self._external_task_handles.pop(task_name, None)


async def _close_listener(listener: object) -> None:
    close = getattr(listener, "close", None)
    if not callable(close):
        return
    if inspect.iscoroutinefunction(close):
        result = close()
    else:
        result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        await result


def _observe_task_exception(task: asyncio.Task[Any]) -> Exception | None:
    if task.cancelled():
        return None
    try:
        exception = task.exception()
    except asyncio.CancelledError:
        return None
    if exception is not None:
        return exception
    return None


def _raise_cleanup_failures(message: str, failures: list[Exception]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0]
    raise ExceptionGroup(message, failures)


__all__ = ["OAuthRuntime"]
