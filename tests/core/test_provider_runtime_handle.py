from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle


class ExceptionObservingTask(asyncio.Task):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.exception_requests = 0

    def exception(self) -> BaseException | None:
        self.exception_requests += 1
        return super().exception()


class RaisingEventsProvider:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def events(self):
        raise self.failure
        yield object()


class RetriableCloseProvider:
    def __init__(self, *, close_failures: int = 1, label: str = "provider") -> None:
        self.close_failures = close_failures
        self.label = label
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_failures > 0:
            self.close_failures -= 1
            raise RuntimeError(f"{self.label} close failed")


class QueueEventsProvider(RetriableCloseProvider):
    def __init__(self, *, close_failures: int = 0, label: str = "provider") -> None:
        super().__init__(close_failures=close_failures, label=label)
        self.queue: asyncio.Queue[object | None] = asyncio.Queue()

    async def emit(self, event: object) -> None:
        await self.queue.put(event)

    async def close(self) -> None:
        await super().close()
        await self.queue.put(None)

    async def events(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


async def wait_until(predicate: Callable[[], bool], *, timeout_s: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_close_failure_retains_provider_for_retry_and_clears_after_success() -> None:
    provider = RetriableCloseProvider(close_failures=1, label="self provider")
    notifications: list[object | None] = []
    handle = ProviderRuntimeHandle(
        name="self_stt",
        provider=provider,
        state_changed=lambda changed: notifications.append(changed.provider),
    )

    with pytest.raises(RuntimeError, match="self provider close failed"):
        await handle.close()

    assert handle.provider is provider
    assert provider.close_calls == 1

    await handle.close()

    assert provider.close_calls == 2
    assert handle.provider is None
    assert notifications[-1] is None


@pytest.mark.asyncio
async def test_replace_provider_starts_new_ingress_when_old_close_fails() -> None:
    processed_events: list[object] = []
    old_provider = QueueEventsProvider(close_failures=1, label="old provider")
    new_provider = QueueEventsProvider(label="new provider")

    async def handle_event(event: object) -> None:
        processed_events.append(event)

    handle = ProviderRuntimeHandle(
        name="self_stt",
        provider=old_provider,
        event_handler=handle_event,
    )

    await handle.start()
    old_event_task = handle.event_task
    assert old_event_task is not None

    with pytest.raises(RuntimeError, match="old provider close failed"):
        await handle.replace_provider(new_provider, start=True)

    assert handle.provider is new_provider
    assert handle.event_task is not None
    assert handle.event_task is not old_event_task

    await old_provider.emit("old stale event")
    await new_provider.emit("new current event")
    await wait_until(lambda: processed_events == ["new current event"])

    await handle.close()

    assert old_provider.close_calls == 2
    assert new_provider.close_calls == 1
    assert handle.provider is None
    assert handle.event_task is None
    assert processed_events == ["new current event"]


@pytest.mark.asyncio
async def test_failed_event_task_exception_is_retrieved_by_owner_done_callback() -> None:
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
        handled_exceptions: list[Exception] = []
        failure = RuntimeError("provider events failed")

        async def handle_event(_event: object) -> None:
            raise AssertionError("provider should fail before yielding events")

        async def handle_exception(exc: Exception) -> None:
            handled_exceptions.append(exc)

        handle = ProviderRuntimeHandle(
            name="self_stt",
            provider=RaisingEventsProvider(failure),
            event_handler=handle_event,
            exception_handler=handle_exception,
        )

        await handle.start()
        task = handle.event_task

        assert isinstance(task, ExceptionObservingTask)
        await wait_until(task.done)
        await asyncio.sleep(0)
        owner_exception_requests = task.exception_requests
    finally:
        loop.set_task_factory(previous_factory)
        for observed_task in observed_tasks:
            if observed_task.done() and observed_task.exception_requests == 0:
                _ = observed_task.exception()

    assert handled_exceptions == [failure]
    assert handle.event_task is None
    assert owner_exception_requests == 1


@pytest.mark.asyncio
async def test_readopted_retired_provider_is_only_closed_as_current_on_shutdown() -> None:
    original = RetriableCloseProvider(close_failures=1, label="original")
    replacement = RetriableCloseProvider(close_failures=0, label="replacement")
    handle = ProviderRuntimeHandle(name="llm", provider=original)

    with pytest.raises(RuntimeError, match="original"):
        await handle.replace_provider(replacement, start=False)

    assert original.close_calls == 1
    assert handle.provider is replacement

    await handle.replace_provider(original, start=False)
    assert replacement.close_calls == 1
    assert handle.provider is original
    assert handle._retired_providers == []

    await handle.close()

    assert original.close_calls == 2
    assert handle._retired_providers == []
