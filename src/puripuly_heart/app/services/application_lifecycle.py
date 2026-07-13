from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


class ApplicationRuntimePort(Protocol):
    async def start(self, *, auto_flush_osc: bool = True) -> None: ...

    async def shutdown(self) -> None: ...


class OverlayRuntimePort(Protocol):
    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


class PresentationHostPort(Protocol):
    async def prepare_presentation(self) -> None: ...

    async def start_rendering(self) -> None: ...

    async def freeze_application_ingress(self) -> None: ...

    async def stop_rendering(self, failures: tuple[BaseException, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class ApplicationLifecycleFailure:
    phase: str
    failure_type: str


class ApplicationLifecycleError(BaseExceptionGroup):
    failures: tuple[ApplicationLifecycleFailure, ...]

    def __new__(
        cls,
        message: str,
        exceptions: list[BaseException],
        failures: tuple[ApplicationLifecycleFailure, ...],
    ):
        instance = super().__new__(cls, message, exceptions)
        instance.failures = failures
        return instance

    def __init__(
        self,
        message: str,
        exceptions: list[BaseException],
        failures: tuple[ApplicationLifecycleFailure, ...],
    ) -> None:
        super().__init__(message, exceptions)


class ApplicationStartupError(BaseExceptionGroup):
    pass


class ApplicationLifecycleOwner:
    def __init__(
        self,
        *,
        runtime: ApplicationRuntimePort | None = None,
        overlay: OverlayRuntimePort | None = None,
        application_adapters=None,  # noqa: ANN001
    ) -> None:
        self._runtime = runtime
        self._overlay = overlay
        self._runtime_owned = runtime is not None
        self._overlay_owned = overlay is not None
        self._application_adapters = application_adapters
        self._application_adapters_closed = False
        self._presentation: PresentationHostPort | None = None
        self._started = False
        self._stopping = False
        self._runtime_started = False
        self._overlay_started = False
        self._runtime_closed = False
        self._overlay_closed = False
        self._presentation_prepared = False
        self._rendering_started = False
        self._ingress_frozen = False
        self._rendering_stopped = False
        self._closed = False
        self._stop_lock = asyncio.Lock()

    def adopt_runtime(self, runtime: ApplicationRuntimePort) -> None:
        if self._runtime_owned:
            raise RuntimeError("application runtime already adopted")
        self._runtime = runtime
        self._runtime_owned = True

    def adopt_overlay(self, overlay: OverlayRuntimePort) -> None:
        if self._overlay_owned:
            raise RuntimeError("overlay runtime already adopted")
        self._overlay = overlay
        self._overlay_owned = True

    def adopt_application_adapters(self, application_adapters) -> None:  # noqa: ANN001
        if self._application_adapters is not None:
            raise RuntimeError("application adapters already adopted")
        self._application_adapters = application_adapters

    def adopt_presentation(self, presentation: PresentationHostPort) -> None:
        if self._presentation is not None:
            raise RuntimeError("presentation already adopted")
        self._presentation = presentation

    async def start(self, presentation: PresentationHostPort | None = None) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("application lifecycle is closed")
        if presentation is not None:
            self.adopt_presentation(presentation)
        presentation = self._presentation
        if presentation is None or self._runtime is None or self._overlay is None:
            raise RuntimeError("application lifecycle construction is incomplete")
        try:
            await presentation.prepare_presentation()
            self._presentation_prepared = True
            await self._runtime.start(auto_flush_osc=True)
            self._runtime_started = True
            await self._overlay.startup()
            self._overlay_started = True
            await presentation.start_rendering()
            self._rendering_started = True
            self._started = True
        except BaseException as startup_failure:
            try:
                await self.stop()
            except BaseException as cleanup_failure:
                raise ApplicationStartupError(
                    "application startup and cleanup failed",
                    [startup_failure, cleanup_failure],
                ) from startup_failure
            raise

    async def stop(self) -> None:
        async with self._stop_lock:
            if self._closed or self._stopping:
                return
            self._stopping = True
            failures: list[BaseException] = []
            facts: list[ApplicationLifecycleFailure] = []

            async def invoke(phase: str, operation) -> bool:  # noqa: ANN001
                try:
                    await operation()
                except asyncio.CancelledError as exc:
                    failures.append(exc)
                    facts.append(ApplicationLifecycleFailure(phase, type(exc).__name__))
                    return False
                except BaseException as exc:
                    failures.append(exc)
                    facts.append(ApplicationLifecycleFailure(phase, type(exc).__name__))
                    return False
                return True

            presentation = self._presentation
            try:
                if (
                    presentation is not None
                    and self._presentation_prepared
                    and not self._ingress_frozen
                ):
                    if await invoke("ingress_freeze", presentation.freeze_application_ingress):
                        self._ingress_frozen = True
                if self._application_adapters is not None and not self._application_adapters_closed:
                    if await invoke("application_adapters", self._application_adapters.close):
                        self._application_adapters_closed = True
                if self._overlay_owned and not self._overlay_closed:
                    if await invoke("overlay_vrc_renderer", self._overlay.shutdown):
                        self._overlay_started = False
                        self._overlay_closed = True
                if self._runtime_owned and not self._runtime_closed:
                    if await invoke("runtime_channels_providers_adapters", self._runtime.shutdown):
                        self._runtime_started = False
                        self._runtime_closed = True
                if (
                    presentation is not None
                    and self._presentation_prepared
                    and not self._rendering_stopped
                ):
                    if await invoke(
                        "ui_logging_handoff",
                        lambda: presentation.stop_rendering(tuple(failures)),
                    ):
                        self._rendering_started = False
                        self._rendering_stopped = True
            finally:
                self._stopping = False

            if not failures:
                self._started = False
                self._closed = True
                return
            raise ApplicationLifecycleError(
                "application lifecycle shutdown failed", failures, tuple(facts)
            )


__all__ = [
    "ApplicationLifecycleError",
    "ApplicationLifecycleFailure",
    "ApplicationLifecycleOwner",
    "ApplicationStartupError",
]
