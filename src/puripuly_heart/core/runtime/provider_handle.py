from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from puripuly_heart.core.runtime.provider_state import ProviderSlot, ProviderStateCell

ProviderEventHandler = Callable[[object], Awaitable[None]]
ProviderExceptionHandler = Callable[[Exception], Awaitable[None] | None]
ProviderStateChanged = Callable[["ProviderRuntimeHandle"], None]


class ProviderRuntimeHandle:
    """Owns one provider resource and its optional provider event loop task."""

    resource_fields = ("provider", "event_task", "generation")
    toggle_off_policy = (
        "STT toggle-off immediately awaits provider.close() without finalizing a pending "
        "utterance; app shutdown and replacement retain bounded awaited close behavior."
    )
    shutdown_policy = (
        "stop ingress, cancel the provider event task, await provider.close(), then close "
        "backend-level resources when the provider exposes them"
    )
    late_callback_rule = "provider event generation rejects stale STT callbacks"

    def __init__(
        self,
        *,
        name: str,
        provider: object | None = None,
        event_handler: ProviderEventHandler | None = None,
        exception_handler: ProviderExceptionHandler | None = None,
        state_changed: ProviderStateChanged | None = None,
        state_cell: ProviderStateCell | None = None,
        slot: ProviderSlot | None = None,
    ) -> None:
        self._name = name
        self._slot: ProviderSlot = slot or _slot_for_name(name)
        if state_cell is not None:
            selected_provider = state_cell.snapshot().slot(self._slot).provider
            if selected_provider is not provider:
                raise ValueError("provider must exactly match the selected state-cell slot")
            self._state_cell = state_cell
        else:
            self._state_cell = ProviderStateCell(**{self._slot: provider})
        self._event_handler = event_handler
        self._exception_handler = exception_handler
        self._state_changed = state_changed
        self._event_task: asyncio.Task[None] | None = None
        self._running = False
        self._closed = False
        self._retired_providers: list[object] = []
        self._lock = asyncio.Lock()

    @property
    def owner_name(self) -> str:
        return f"ProviderRuntimeHandle:{self._name}"

    @property
    def provider(self) -> object | None:
        return self._state_cell.snapshot().slot(self._slot).provider

    @property
    def event_task(self) -> asyncio.Task[None] | None:
        return self._event_task

    @property
    def generation(self) -> int:
        return self._state_cell.snapshot().slot(self._slot).generation

    @property
    def has_resources(self) -> bool:
        return (
            self.provider is not None
            or self._event_task is not None
            or bool(self._retired_providers)
        )

    def current_provider_generation(self) -> tuple[object | None, int]:
        """Capture the current provider identity and generation for an in-flight call."""

        state = self._state_cell.snapshot().slot(self._slot)
        return state.provider, state.generation

    def is_current_provider_generation(self, *, provider: object, generation: int) -> bool:
        """Return whether a captured provider/generation is still current."""

        state = self._state_cell.snapshot().slot(self._slot)
        return generation == state.generation and provider is state.provider and not self._closed

    def lifecycle_owner_snapshot(self) -> dict[str, object]:
        return {
            "owner": self.owner_name,
            "resource_fields": self.resource_fields,
            "stop_ingress": "detach from hub/runtime coordinator",
            "toggle_off_policy": self.toggle_off_policy,
            "shutdown_policy": self.shutdown_policy,
            "late_callback_rule": self.late_callback_rule,
        }

    async def start(self) -> None:
        async with self._lock:
            self._running = True
            self._closed = False
            self._start_event_loop_if_needed()

    async def start_if_provider(self, expected_provider: object) -> bool:
        async with self._lock:
            if self.provider is not expected_provider:
                return False
            self._running = True
            self._closed = False
            self._start_event_loop_if_needed()
            return True

    async def replace_provider(self, provider: object | None, *, start: bool) -> object | None:
        async with self._lock:
            old_provider = self.provider
            await self._cancel_event_task()
            self._state_cell.replace(self._slot, provider)
            self._remove_retired_provider(provider)
            self._closed = False
            self._notify_state_changed()
            if start:
                self._running = True
                self._start_event_loop_if_needed()
            if old_provider is not None and old_provider is not provider:
                try:
                    await self._close_provider_for_shutdown(old_provider)
                except Exception:
                    self._retain_retired_provider(old_provider)
                    raise
            return old_provider

    async def drain_for_toggle_off(self) -> None:
        async with self._lock:
            provider = self.provider
            if provider is not None:
                stop_for_toggle_off = getattr(provider, "stop_for_toggle_off", None)
                if callable(stop_for_toggle_off):
                    result = stop_for_toggle_off()
                    if inspect.isawaitable(result):
                        await result
                else:
                    await _call_async_method(provider, "close")
            self._start_event_loop_if_needed()

    async def stop_ingress(self) -> None:
        async with self._lock:
            self._running = False
            await self._cancel_event_task()

    async def close(self) -> None:
        async with self._lock:
            if self._closed and not self.has_resources:
                return
            self._closed = True
            self._running = False
            await self._cancel_event_task()
            failures: list[Exception] = []
            provider = self.provider
            if provider is not None:
                try:
                    await self._close_provider_for_shutdown(provider)
                except Exception as exc:
                    failures.append(exc)
                else:
                    if self.provider is provider:
                        self._state_cell.replace(self._slot, None)
                        self._notify_state_changed()
            failures.extend(await self._close_retired_providers())
            _raise_close_failures(failures, f"{self.owner_name} provider close failed")

    def _start_event_loop_if_needed(self) -> None:
        if not self._running or self._event_handler is None or self.provider is None:
            self._notify_state_changed()
            return
        if self._event_task is not None and not self._event_task.done():
            self._notify_state_changed()
            return
        provider, generation = self.current_provider_generation()
        assert provider is not None
        self._event_task = asyncio.create_task(
            self._run_event_loop(provider=provider, generation=generation),
            name=f"{self.owner_name}:events",
        )
        self._event_task.add_done_callback(self._on_event_task_done)
        self._notify_state_changed()

    async def _run_event_loop(self, *, provider: object, generation: int) -> None:
        try:
            async for event in provider.events():  # type: ignore[attr-defined]
                if not self._is_current(provider=provider, generation=generation):
                    continue
                assert self._event_handler is not None
                await self._event_handler(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._exception_handler is not None:
                result = self._exception_handler(exc)
                if inspect.isawaitable(result):
                    await result
            raise

    def _is_current(self, *, provider: object, generation: int) -> bool:
        return self._running and self.is_current_provider_generation(
            provider=provider,
            generation=generation,
        )

    async def _cancel_event_task(self) -> None:
        task = self._event_task
        if task is None:
            self._notify_state_changed()
            return
        self._event_task = None
        self._notify_state_changed()
        if task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _on_event_task_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
        if self._event_task is task:
            self._event_task = None
            self._notify_state_changed()

    async def _close_provider_for_shutdown(self, provider: object | None) -> None:
        if provider is None:
            return
        close_backend = getattr(provider, "close_backend", None)
        if callable(close_backend):
            result = close_backend()
            if inspect.isawaitable(result):
                await result
            return
        await _call_async_method(provider, "close")

    def _retain_retired_provider(self, provider: object) -> None:
        if provider is self.provider:
            return
        if any(retired_provider is provider for retired_provider in self._retired_providers):
            return
        self._retired_providers.append(provider)

    def _remove_retired_provider(self, provider: object | None) -> None:
        if provider is None:
            return
        self._retired_providers = [
            retired_provider
            for retired_provider in self._retired_providers
            if retired_provider is not provider
        ]

    async def _close_retired_providers(self) -> list[Exception]:
        failures: list[Exception] = []
        still_retired: list[object] = []
        for provider in self._retired_providers:
            try:
                await self._close_provider_for_shutdown(provider)
            except Exception as exc:
                failures.append(exc)
                still_retired.append(provider)
        self._retired_providers = still_retired
        return failures

    def _notify_state_changed(self) -> None:
        if self._state_changed is not None:
            self._state_changed(self)


async def _call_async_method(resource: object, method_name: str) -> None:
    method = getattr(resource, method_name, None)
    if not callable(method):
        return
    result: Any = method()
    if inspect.isawaitable(result):
        await result


def _raise_close_failures(failures: list[Exception], message: str) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0]
    raise ExceptionGroup(message, failures)


__all__ = ["ProviderRuntimeHandle"]


def _slot_for_name(name: str) -> ProviderSlot:
    if name in {"llm", "self_stt", "peer_stt"}:
        return name
    return "llm"
