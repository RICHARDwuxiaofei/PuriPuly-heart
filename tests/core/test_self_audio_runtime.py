from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from puripuly_heart.core.runtime.self_audio import SelfAudioRuntime


class RecordingVadSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def handle_vad_event(self, event: object) -> None:
        self.events.append(event)


class RetriableCloseSource:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure


async def wait_until(predicate: Callable[[], bool], *, timeout_s: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0)


@pytest.mark.parametrize("method_name", ["stop", "close"])
@pytest.mark.asyncio
async def test_source_close_failure_from_stop_or_close_raises_retains_and_retries(
    method_name: str,
) -> None:
    runtime = SelfAudioRuntime()
    source_failure = RuntimeError("microphone close failed")
    source = RetriableCloseSource(source_failure)
    loop_started = asyncio.Event()
    loop_release = asyncio.Event()

    async def run_loop() -> None:
        loop_started.set()
        await loop_release.wait()

    runtime.start(source=source, vad=object(), run_loop=run_loop)
    await wait_until(loop_started.is_set)

    with pytest.raises(RuntimeError, match="microphone close failed") as exc_info:
        await getattr(runtime, method_name)()

    assert exc_info.value is source_failure
    assert runtime.audio_source is source
    assert runtime.last_close_exception is source_failure
    assert source.close_calls == 1

    await runtime.close()

    assert runtime.audio_source is None
    assert runtime.last_close_exception is None
    assert source.close_calls == 2


@pytest.mark.asyncio
async def test_completed_mic_loop_clears_task_invalidates_generation_and_notifies_state() -> None:
    state_changes: list[tuple[asyncio.Task[None] | None, int]] = []
    runtime = SelfAudioRuntime(
        state_changed=lambda owner: state_changes.append((owner.loop_task, owner.generation)),
    )
    sink = RecordingVadSink()

    async def run_loop_once() -> None:
        return None

    runtime.start(source=object(), vad=object(), run_loop=run_loop_once)
    guarded_sink = runtime.guard_vad_sink(sink)
    guarded_generation = getattr(guarded_sink, "generation")
    first_task = runtime.loop_task

    assert first_task is not None
    await wait_until(first_task.done)
    await asyncio.sleep(0)

    assert runtime.loop_task is None
    assert runtime.generation != guarded_generation
    assert state_changes[-1] == (None, runtime.generation)

    await guarded_sink.handle_vad_event("late-event")  # type: ignore[attr-defined]
    assert sink.events == []

    await runtime.stop()

    second_loop_started = asyncio.Event()
    second_loop_release = asyncio.Event()

    async def run_second_loop() -> None:
        second_loop_started.set()
        await second_loop_release.wait()

    runtime.start(source=object(), vad=object(), run_loop=run_second_loop)
    second_task = runtime.loop_task

    assert second_task is not None
    assert second_task is not first_task
    await wait_until(second_loop_started.is_set)

    second_loop_release.set()
    await wait_until(second_task.done)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_start_after_completed_loop_without_stop_keeps_retained_source_and_vad() -> None:
    runtime = SelfAudioRuntime()
    first_source = object()
    first_vad = object()

    async def run_loop_once() -> None:
        return None

    runtime.start(source=first_source, vad=first_vad, run_loop=run_loop_once)
    first_task = runtime.loop_task

    assert first_task is not None
    await wait_until(first_task.done)
    await asyncio.sleep(0)
    assert runtime.loop_task is None
    assert runtime.audio_source is first_source
    assert runtime.vad is first_vad

    second_source = object()
    second_vad = object()
    second_loop_started = asyncio.Event()
    second_loop_release = asyncio.Event()

    async def run_second_loop() -> None:
        second_loop_started.set()
        await second_loop_release.wait()

    try:
        runtime.start(source=second_source, vad=second_vad, run_loop=run_second_loop)

        assert runtime.loop_task is None
        assert runtime.audio_source is first_source
        assert runtime.vad is first_vad
        assert not second_loop_started.is_set()
    finally:
        second_loop_release.set()
        await runtime.stop()


@pytest.mark.asyncio
async def test_failed_mic_loop_clears_task_after_error_handler() -> None:
    state_changes: list[asyncio.Task[None] | None] = []
    errors: list[Exception] = []
    runtime = SelfAudioRuntime(
        state_changed=lambda owner: state_changes.append(owner.loop_task),
        error_handler=errors.append,
    )
    failure = RuntimeError("mic loop failed")

    async def fail_loop() -> None:
        raise failure

    runtime.start(source=object(), vad=object(), run_loop=fail_loop)
    task = runtime.loop_task

    assert task is not None
    await wait_until(task.done)
    await asyncio.sleep(0)

    assert errors == [failure]
    assert runtime.loop_task is None
    assert state_changes[-1] is None

    _ = task.exception()
