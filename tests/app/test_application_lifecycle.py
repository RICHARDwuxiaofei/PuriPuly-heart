from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.app.services.application_lifecycle import (
    ApplicationLifecycleError,
    ApplicationLifecycleOwner,
    ApplicationStartupError,
)


class Resource:
    def __init__(self, events: list[str], start: str, stop: str) -> None:
        self.events = events
        self.start_event = start
        self.stop_event = stop
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_stop = False

    async def start(self, **_kwargs) -> None:
        self.start_calls += 1
        self.events.append(self.start_event)

    async def startup(self) -> None:
        await self.start()

    async def shutdown(self) -> None:
        self.stop_calls += 1
        self.events.append(self.stop_event)
        if self.fail_stop:
            self.fail_stop = False
            raise RuntimeError(self.stop_event)

    async def close(self) -> None:
        await self.shutdown()


class Presentation:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.fail_prepare = False
        self.fail_freeze = False
        self.fail_stop_rendering = False
        self.freeze_calls = 0
        self.stop_rendering_calls = 0

    async def prepare_presentation(self) -> None:
        self.events.append("ui_prepare")
        if self.fail_prepare:
            raise RuntimeError("prepare")

    async def start_rendering(self) -> None:
        self.events.append("ui_render_start")

    async def freeze_application_ingress(self) -> None:
        self.freeze_calls += 1
        self.events.append("ui_freeze")
        if self.fail_freeze:
            self.fail_freeze = False
            raise RuntimeError("freeze")

    async def stop_rendering(self, failures) -> None:  # noqa: ANN001
        self.stop_rendering_calls += 1
        self.events.append(f"ui_logging_stop:{len(failures)}")
        if self.fail_stop_rendering:
            self.fail_stop_rendering = False
            raise RuntimeError("render")


def composition():  # noqa: ANN201
    events: list[str] = []
    runtime = Resource(events, "runtime_start", "runtime_stop")
    overlay = Resource(events, "overlay_start", "overlay_stop")
    presentation = Presentation(events)
    adapters = Resource(events, "unused", "application_adapters_stop")
    owner = ApplicationLifecycleOwner(
        runtime=runtime, overlay=overlay, application_adapters=adapters
    )
    return events, runtime, overlay, adapters, presentation, owner


@pytest.mark.asyncio
async def test_application_owner_orders_startup_and_shutdown_exactly_once() -> None:
    events, runtime, overlay, adapters, presentation, owner = composition()

    await owner.start(presentation)
    await owner.stop()
    await owner.stop()

    assert events == [
        "ui_prepare",
        "runtime_start",
        "overlay_start",
        "ui_render_start",
        "ui_freeze",
        "application_adapters_stop",
        "overlay_stop",
        "runtime_stop",
        "ui_logging_stop:0",
    ]
    assert runtime.stop_calls == overlay.stop_calls == 1
    assert adapters.stop_calls == 1


@pytest.mark.asyncio
async def test_partial_start_can_be_stopped_idempotently() -> None:
    events, runtime, overlay, adapters, presentation, owner = composition()
    presentation.fail_prepare = True

    with pytest.raises(RuntimeError, match="prepare"):
        await owner.start(presentation)
    await owner.stop()

    assert events == [
        "ui_prepare",
        "application_adapters_stop",
        "overlay_stop",
        "runtime_stop",
    ]
    assert runtime.stop_calls == overlay.stop_calls == adapters.stop_calls == 1


@pytest.mark.asyncio
async def test_shutdown_aggregates_failures_and_retries_only_failed_owner() -> None:
    events, runtime, overlay, _adapters, presentation, owner = composition()
    await owner.start(presentation)
    runtime.fail_stop = True
    overlay.fail_stop = True

    with pytest.raises(ApplicationLifecycleError) as raised:
        await owner.stop()

    assert [fact.phase for fact in raised.value.failures] == [
        "overlay_vrc_renderer",
        "runtime_channels_providers_adapters",
    ]
    await owner.stop()
    assert runtime.stop_calls == overlay.stop_calls == 2
    assert events.count("ui_freeze") == 1


@pytest.mark.asyncio
async def test_cancellation_during_start_runs_owned_cleanup_without_surviving_task() -> None:
    events, runtime, overlay, _adapters, presentation, owner = composition()
    entered = asyncio.Event()

    async def cancelled_start(**_kwargs) -> None:
        entered.set()
        await asyncio.Future()

    runtime.start = cancelled_start
    task = asyncio.create_task(owner.start(presentation))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.done()
    assert events == [
        "ui_prepare",
        "ui_freeze",
        "application_adapters_stop",
        "overlay_stop",
        "runtime_stop",
        "ui_logging_stop:0",
    ]


@pytest.mark.asyncio
async def test_shutdown_retries_only_failed_ingress_and_rendering_phases() -> None:
    _events, runtime, overlay, adapters, presentation, owner = composition()
    await owner.start(presentation)
    presentation.fail_freeze = True
    presentation.fail_stop_rendering = True

    with pytest.raises(ApplicationLifecycleError) as raised:
        await owner.stop()

    assert [fact.phase for fact in raised.value.failures] == [
        "ingress_freeze",
        "ui_logging_handoff",
    ]
    assert runtime.stop_calls == overlay.stop_calls == adapters.stop_calls == 1
    await owner.stop()
    assert runtime.stop_calls == overlay.stop_calls == adapters.stop_calls == 1
    assert presentation.freeze_calls == presentation.stop_rendering_calls == 2


@pytest.mark.asyncio
async def test_failed_ingress_retries_after_all_later_phases_succeed() -> None:
    _events, runtime, overlay, adapters, presentation, owner = composition()
    await owner.start(presentation)
    presentation.fail_freeze = True

    with pytest.raises(ApplicationLifecycleError):
        await owner.stop()
    await owner.stop()

    assert presentation.freeze_calls == 2
    assert presentation.stop_rendering_calls == 1
    assert runtime.stop_calls == overlay.stop_calls == adapters.stop_calls == 1


@pytest.mark.asyncio
async def test_partial_start_and_cleanup_failure_aggregate_and_remain_retryable() -> None:
    _events, runtime, overlay, adapters, presentation, owner = composition()
    overlay.fail_stop = True

    async def fail_start(**_kwargs) -> None:
        raise ValueError("startup")

    runtime.start = fail_start
    with pytest.raises(ApplicationStartupError) as raised:
        await owner.start(presentation)

    assert isinstance(raised.value.exceptions[0], ValueError)
    assert isinstance(raised.value.exceptions[1], ApplicationLifecycleError)
    assert adapters.stop_calls == runtime.stop_calls == overlay.stop_calls == 1
    await owner.stop()
    assert adapters.stop_calls == runtime.stop_calls == 1
    assert overlay.stop_calls == 2


@pytest.mark.asyncio
async def test_partial_start_persistent_cleanup_failure_keeps_owner_reachable() -> None:
    _events, runtime, overlay, _adapters, presentation, owner = composition()

    async def fail_start(**_kwargs) -> None:
        raise ValueError("startup")

    async def fail_overlay() -> None:
        overlay.stop_calls += 1
        raise RuntimeError("persistent")

    runtime.start = fail_start
    overlay.shutdown = fail_overlay
    with pytest.raises(ApplicationStartupError):
        await owner.start(presentation)
    with pytest.raises(ApplicationLifecycleError):
        await owner.stop()

    assert overlay.stop_calls == 2
    assert owner._closed is False
