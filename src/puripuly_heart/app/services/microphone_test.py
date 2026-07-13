from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

import numpy as np

from puripuly_heart.app.ports.application_settings import SettingsField
from puripuly_heart.app.ports.ui_settings import MicrophoneTestResult, MicrophoneTestStatus
from puripuly_heart.core.runtime.mic_test import MicTestRuntime


class MicrophoneSourcePort(Protocol):
    def frames(self): ...  # noqa: ANN201

    async def close(self) -> None: ...


class MicrophoneSourceFactoryPort(Protocol):
    async def create(self, *, host_api: str, device: str) -> MicrophoneSourcePort | None: ...


class ApplicationMicrophoneTestService:
    def __init__(
        self,
        *,
        settings_queries,
        runtime: MicTestRuntime,
        source_factory: MicrophoneSourceFactoryPort,
        diagnostics: Callable[[str, str | None], None] | None = None,
    ) -> None:  # noqa: ANN001
        self._settings_queries = settings_queries
        self._runtime = runtime
        self._source_factory = source_factory
        self._diagnostics = diagnostics
        self._last_level = 0.0
        self._last_terminal_detail: str | None = None

    @property
    def active(self) -> bool:
        task = self._runtime.session_task
        return task is not None and not task.done()

    @property
    def last_level(self) -> float:
        return self._last_level

    @property
    def is_closed(self) -> bool:
        return self._runtime.is_closed

    async def start(self) -> MicrophoneTestResult:
        if self.active:
            return MicrophoneTestResult(MicrophoneTestStatus.ALREADY_ACTIVE)
        if self._runtime.source is not None or self._runtime.pending_frame_task is not None:
            await self._runtime.stop()
        snapshot = await self._settings_queries.snapshot()
        values = dict(snapshot.leaves)
        host_api = str(values.get((SettingsField.AUDIO_INPUT_HOST_API.value,), ""))
        device = str(values.get((SettingsField.AUDIO_INPUT_DEVICE.value,), ""))
        self._last_terminal_detail = None
        try:
            task = self._runtime.start(
                lambda generation: self._run(generation, host_api=host_api, device=device)
            )
        except RuntimeError:
            return MicrophoneTestResult(MicrophoneTestStatus.ALREADY_ACTIVE)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if task.done() and self._last_terminal_detail == "device_unavailable":
            return MicrophoneTestResult(MicrophoneTestStatus.UNAVAILABLE, "device_unavailable")
        if task.done() and self._last_terminal_detail is not None:
            return MicrophoneTestResult(MicrophoneTestStatus.FAILED, self._last_terminal_detail)
        if task.done() and task.exception() is not None:
            return MicrophoneTestResult(MicrophoneTestStatus.FAILED, "session_start_failed")
        return MicrophoneTestResult(
            MicrophoneTestStatus.STARTED, generation=self._runtime.generation
        )

    async def stop(self) -> MicrophoneTestResult:
        await self._runtime.stop()
        self._last_level = 0.0
        return MicrophoneTestResult(MicrophoneTestStatus.STOPPED)

    async def close(self) -> None:
        await self._runtime.close()

    async def _run(self, generation: int, *, host_api: str, device: str) -> None:
        source = None
        pending = None
        try:
            source = await self._source_factory.create(host_api=host_api, device=device)
            if source is None:
                self._last_terminal_detail = "device_unavailable"
                self._emit("unavailable", "device_unavailable")
                return
            if not self._runtime.attach_source(source, generation=generation):
                await source.close()
                return
            frames = source.frames()
            while self._runtime.is_current_generation(generation):
                pending = self._runtime.create_frame_task(anext(frames), generation=generation)
                try:
                    frame = await pending
                except StopAsyncIteration:
                    break
                try:
                    samples = np.asarray(frame.samples, dtype=np.float32)
                    finite = np.abs(samples[np.isfinite(samples)])
                    self._last_level = 0.0 if finite.size == 0 else min(1.0, float(np.max(finite)))
                except (TypeError, ValueError):
                    self._last_level = 0.0
                    self._emit("invalid_frame", "invalid_samples")
                self._emit("frame", None)
                pending = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_terminal_detail = type(exc).__name__
            self._emit("failed", type(exc).__name__)
        finally:
            if pending is not None:
                await self._runtime.cancel_frame_task(pending)
            if source is not None:
                await self._runtime.close_source(source)
            self._last_level = 0.0

    def _emit(self, event: str, detail: str | None) -> None:
        if self._diagnostics is not None:
            self._diagnostics(event, detail)


__all__ = ["ApplicationMicrophoneTestService", "MicrophoneSourceFactoryPort"]
