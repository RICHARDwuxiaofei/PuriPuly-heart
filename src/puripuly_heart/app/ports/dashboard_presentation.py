from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from puripuly_heart.domain.events import UIEvent


@dataclass(frozen=True, slots=True)
class DashboardPresentationEventContext:
    source_language: str | None
    target_language: str | None
    translation_enabled: bool
    self_stt_state: str | None
    runtime_logging_mode: str | None


class DashboardPresentationContextPort(Protocol):
    def current_event_context(self) -> DashboardPresentationEventContext: ...

    def clear_managed_auth_pending(self) -> None: ...

    def observe_translation_success(self) -> None: ...

    async def record_translation_success(self) -> None: ...


class PresentationRuntimeLoggingPort(Protocol):
    @property
    def mode(self) -> str: ...

    def emit_basic(self, message: str, *, level: int) -> None: ...

    def emit_detailed(self, message: str, *, level: int) -> None: ...


class PresentationEventBridgePort(Protocol):
    async def run(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PresentationEventBridgeRequest:
    event_queue: asyncio.Queue[UIEvent]
    context: DashboardPresentationContextPort
    runtime_logging: PresentationRuntimeLoggingPort | None


class PresentationEventBridgeFactoryPort(Protocol):
    def create_event_bridge(
        self, request: PresentationEventBridgeRequest
    ) -> PresentationEventBridgePort: ...


class PresentationEventRuntimePort(Protocol):
    def start_ui_event_bridge(
        self, bridge: PresentationEventBridgePort
    ) -> asyncio.Task[object]: ...

    async def stop_ui_event_bridge(self) -> None: ...


class DashboardPresentationViewPort(Protocol):
    async def prepare_dashboard(self) -> None: ...

    async def start_dashboard(self) -> None: ...

    async def freeze_dashboard_ingress(self) -> None: ...

    async def stop_dashboard(self, failures: tuple[BaseException, ...]) -> None: ...


__all__ = [
    "DashboardPresentationContextPort",
    "DashboardPresentationEventContext",
    "DashboardPresentationViewPort",
    "PresentationEventBridgeFactoryPort",
    "PresentationEventBridgePort",
    "PresentationEventBridgeRequest",
    "PresentationEventRuntimePort",
    "PresentationRuntimeLoggingPort",
]
