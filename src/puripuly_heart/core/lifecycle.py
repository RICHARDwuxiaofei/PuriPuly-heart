from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from puripuly_heart.core.messages import (
    CONTENT_POLICY_METADATA_ONLY,
    DIAGNOSTIC_CATEGORY_LIFECYCLE,
    DIAGNOSTIC_VISIBILITY_DETAILED,
    SEVERITY_ERROR,
    ErrorDiagnostics,
)
from puripuly_heart.core.observability import DiagnosticEvent, DiagnosticsSink

_TaskResultT = TypeVar("_TaskResultT")
_DIAGNOSTIC_FIELD_VALUE_MAX_LENGTH = 128

CloseCallback = Callable[[], Awaitable[None] | None]


class LifecycleScopeClosedError(RuntimeError):
    """Raised when new work is registered after a lifecycle scope is closed."""


class LifecycleTaskNameInUseError(RuntimeError):
    """Raised when active work is registered under an existing task name."""


class LifecycleDiagnosticsUnavailableError(RuntimeError):
    """Raised when lifecycle failures exist but no diagnostics sink is available."""

    def __init__(self, scope_name: str, diagnostics: tuple[DiagnosticEvent, ...]) -> None:
        self.scope_name = scope_name
        self.diagnostics = diagnostics
        super().__init__(
            "Lifecycle diagnostics unavailable "
            f"for scope {scope_name!r}: {len(diagnostics)} event(s) pending"
        )


@dataclass(frozen=True, slots=True)
class _RegisteredCloseCallback:
    name: str
    callback: CloseCallback


class LifecycleScope:
    """Owns named background tasks and close callbacks for one runtime scope."""

    def __init__(
        self,
        name: str,
        *,
        diagnostics_sink: DiagnosticsSink | None = None,
    ) -> None:
        self._name = name
        self._diagnostics_sink = diagnostics_sink
        self._closed = False
        self._closing = False
        self._close_completed = False
        self._close_lock = asyncio.Lock()
        self._tasks_by_name: dict[str, asyncio.Task[Any]] = {}
        self._task_names: dict[asyncio.Task[Any], str] = {}
        self._diagnosed_tasks: set[asyncio.Task[Any]] = set()
        self._close_callbacks: list[_RegisteredCloseCallback] = []
        self._pending_diagnostics: list[DiagnosticEvent] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def active_task_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks_by_name))

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, _TaskResultT],
        *,
        name: str,
    ) -> asyncio.Task[_TaskResultT]:
        if self._closed:
            coroutine.close()
            raise LifecycleScopeClosedError(f"LifecycleScope {self._name!r} is closed to new tasks")
        if name in self._tasks_by_name:
            coroutine.close()
            raise LifecycleTaskNameInUseError(
                f"LifecycleScope {self._name!r} already has task {name!r}"
            )

        task = asyncio.create_task(coroutine, name=f"{self._name}:{name}")
        self._tasks_by_name[name] = task
        self._task_names[task] = name
        task.add_done_callback(self._on_task_done)
        return task

    def register_close_callback(self, name: str, callback: CloseCallback) -> None:
        if self._closed:
            raise LifecycleScopeClosedError(
                f"LifecycleScope {self._name!r} is closed to new callbacks"
            )
        self._close_callbacks.append(_RegisteredCloseCallback(name=name, callback=callback))

    async def close(self) -> None:
        if self._close_completed:
            return

        async with self._close_lock:
            if self._close_completed:
                return

            self._closed = True
            self._closing = True
            try:
                await self._cancel_and_gather_tasks()
                await self._run_close_callbacks()
                await self._emit_pending_diagnostics()
                self._close_completed = True
            finally:
                self._closing = False

    async def _cancel_and_gather_tasks(self) -> None:
        task_entries = tuple(self._task_names.items())
        already_done = {task for task, _task_name in task_entries if task.done()}
        for task, _task_name in task_entries:
            if not task.done():
                task.cancel()

        if not task_entries:
            return

        results = await asyncio.gather(
            *(task for task, _task_name in task_entries),
            return_exceptions=True,
        )
        for (task, task_name), result in zip(task_entries, results, strict=True):
            self._tasks_by_name.pop(task_name, None)
            self._task_names.pop(task, None)
            if task in self._diagnosed_tasks:
                continue
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                phase = "task_done" if task in already_done else "task_close"
                self._queue_task_exception_diagnostic(task, task_name, result, phase)

    async def _run_close_callbacks(self) -> None:
        while self._close_callbacks:
            callback = self._close_callbacks[0]
            try:
                result = callback.callback()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self._pending_diagnostics.append(
                    self._diagnostic_event(
                        phase="close_callback",
                        callback_name=callback.name,
                        exception=exc,
                    )
                )
            self._close_callbacks.pop(0)

    async def _emit_pending_diagnostics(self) -> None:
        if not self._pending_diagnostics:
            return

        if self._diagnostics_sink is None:
            diagnostics = tuple(self._pending_diagnostics)
            self._pending_diagnostics.clear()
            raise LifecycleDiagnosticsUnavailableError(self._name, diagnostics)

        while self._pending_diagnostics:
            event = self._pending_diagnostics[0]
            await self._diagnostics_sink.emit_diagnostic(event)
            self._pending_diagnostics.pop(0)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        task_name = self._task_names.pop(task, None)
        if task_name is None:
            return
        self._tasks_by_name.pop(task_name, None)

        if task.cancelled():
            return
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            return
        if exception is None:
            return
        self._queue_task_exception_diagnostic(task, task_name, exception, "task_done")

    def _queue_task_exception_diagnostic(
        self,
        task: asyncio.Task[Any],
        task_name: str,
        exception: BaseException,
        phase: str,
    ) -> None:
        self._diagnosed_tasks.add(task)
        self._pending_diagnostics.append(
            self._diagnostic_event(
                phase=phase,
                task_name=task_name,
                exception=exception,
            )
        )

    def _diagnostic_event(
        self,
        *,
        phase: str,
        exception: BaseException,
        task_name: str | None = None,
        callback_name: str | None = None,
    ) -> DiagnosticEvent:
        fields = {
            "scope_name": _safe_field_value(self._name),
            "phase": _safe_field_value(phase),
            "exception_class": _safe_field_value(type(exception).__name__),
        }
        if task_name is not None:
            fields["task_name"] = _safe_field_value(task_name)
        if callback_name is not None:
            fields["callback_name"] = _safe_field_value(callback_name)

        diagnostics = ErrorDiagnostics(
            component="lifecycle.scope",
            operation=phase,
            code="lifecycle_exception",
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            visibility=DIAGNOSTIC_VISIBILITY_DETAILED,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            status_code=None,
            retry_after_ms=None,
            fields=fields,
        )
        return DiagnosticEvent(
            category=DIAGNOSTIC_CATEGORY_LIFECYCLE,
            severity=SEVERITY_ERROR,
            visibility=DIAGNOSTIC_VISIBILITY_DETAILED,
            content_policy=CONTENT_POLICY_METADATA_ONLY,
            correlation_id=None,
            diagnostics=diagnostics,
            fields=fields,
        )


def _safe_field_value(value: str) -> str:
    if len(value) <= _DIAGNOSTIC_FIELD_VALUE_MAX_LENGTH:
        return value
    return f"{value[: _DIAGNOSTIC_FIELD_VALUE_MAX_LENGTH - 1]}…"


__all__ = [
    "CloseCallback",
    "LifecycleDiagnosticsUnavailableError",
    "LifecycleScope",
    "LifecycleScopeClosedError",
    "LifecycleTaskNameInUseError",
]
