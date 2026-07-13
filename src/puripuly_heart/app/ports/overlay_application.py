from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from puripuly_heart.config.overlay_calibration import OverlayCalibration

from .post_commit_runtime import (
    DashboardRetryFactsDirective,
    OverlayOscDirective,
)

OverlayLifecycleState = Literal["off", "starting", "connected", "stopping", "failed"]


@dataclass(frozen=True, slots=True)
class OverlayLifecycleConfiguration:
    enabled: bool
    directive: OverlayOscDirective
    locale: str
    runtime_logging_mode: str
    startup_timeout_ms: int
    shutdown_grace_s: float = 0.05


@dataclass(frozen=True, slots=True)
class OverlayLifecycleSnapshot:
    state: OverlayLifecycleState
    failure_reason: str | None
    active_target: str | None
    overlay_instance_id: str | None


@dataclass(frozen=True, slots=True)
class OverlayOscRuntimeSnapshot:
    directive: OverlayOscDirective | None
    dashboard_facts: DashboardRetryFactsDirective | None
    calibration: OverlayCalibration
    interaction_mode: str


OverlayStateListener = Callable[[OverlayLifecycleSnapshot], None]


class AudioCaptureGatePort(Protocol):
    enabled: bool
    receiver_active: bool

    def process_chunk(self, chunk: Any) -> Any: ...

    def reset(self) -> None: ...


class OverlayApplicationCommandPort(Protocol):
    async def startup(self) -> None: ...

    def bind_runtime_host(self, host: object) -> None: ...

    def bind_desktop_operational_state(
        self,
        persist_bounds: Callable[[dict[str, int | float]], Awaitable[None]],
        reset_position: Callable[[], Awaitable[None]],
    ) -> None: ...

    def configure(self, configuration: OverlayLifecycleConfiguration) -> None: ...

    def configure_intent(
        self,
        settings: object,
        *,
        enabled: bool,
        runtime_logging_mode: str,
        interaction_mode: str,
    ) -> None: ...

    async def set_enabled(self, enabled: bool) -> None: ...

    async def apply_configuration(self, configuration: OverlayLifecycleConfiguration) -> bool: ...

    async def apply_intent(
        self,
        settings: object,
        *,
        enabled: bool,
        runtime_logging_mode: str,
        interaction_mode: str,
    ) -> bool: ...

    async def send_desktop_control(self, payload: Mapping[str, object]) -> bool: ...

    def drain_pending_desktop_user_bounds_events(self) -> None: ...

    async def persist_desktop_bounds(self, bounds: Mapping[str, int | float]) -> None: ...

    async def reset_desktop_position(self) -> None: ...

    async def prepare_desktop_size_change(self) -> None: ...

    def apply_desktop_interaction_mode_event(self, mode: str) -> None: ...

    async def set_logging_mode(self, mode: str) -> None: ...

    async def shutdown(self) -> None: ...


class OverlayApplicationStatePort(Protocol):
    def lifecycle_snapshot(self) -> OverlayLifecycleSnapshot: ...

    def runtime_snapshot(self) -> OverlayOscRuntimeSnapshot: ...

    def subscribe(self, listener: OverlayStateListener) -> Callable[[], None]: ...
