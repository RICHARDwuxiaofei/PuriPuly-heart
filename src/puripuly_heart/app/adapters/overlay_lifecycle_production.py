from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from puripuly_heart.app.ports.overlay_application import (
    OverlayLifecycleConfiguration,
    OverlayLifecycleSnapshot,
    OverlayOscRuntimeSnapshot,
    OverlayStateListener,
)
from puripuly_heart.app.ports.post_commit_runtime import OverlayOscDirective
from puripuly_heart.app.services.overlay_osc_application_runtime import (
    OverlayBridgePort,
    OverlayOscApplicationRuntime,
)
from puripuly_heart.config.resolved import resolve_desktop_overlay_size
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.overlay.process import (
    DefaultOverlayProcessRunner,
    DesktopFletOverlayRunner,
    OverlayProcessManager,
    OverlayProcessRunner,
)

logger = logging.getLogger(__name__)


def resolve_overlay_lifecycle_configuration(
    settings: AppSettingsVNext, *, enabled: bool = False
) -> OverlayLifecycleConfiguration:
    intent = settings.intent
    overlay = intent.overlay
    desktop = overlay.desktop_flet
    width, height = resolve_desktop_overlay_size(desktop.size_preset)
    directive = OverlayOscDirective(
        "overlay_osc",
        overlay.target,
        overlay.show_translation,
        overlay.show_peer_original,
        intent.osc.host,
        intent.osc.port,
        intent.osc.chatbox_address,
        intent.osc.chatbox_send,
        intent.osc.chatbox_clear,
        intent.osc.chatbox_max_chars,
        intent.osc.vrc_mic_intercept,
        intent.osc.chatbox_include_source,
        overlay.calibration.copy(),
        {
            "size_preset": desktop.size_preset,
            "size": {"width": width, "height": height},
            "position": {"x": desktop.position.x, "y": desktop.position.y},
            "visual": {"background_alpha": desktop.visual.background_alpha},
            "interaction_mode": "edit",
        },
    )
    return OverlayLifecycleConfiguration(enabled, directive, intent.ui.locale, "basic", 3000)


def _resolve_legacy_overlay_lifecycle_configuration(
    settings: object,
    *,
    enabled: bool,
    runtime_logging_mode: str,
    interaction_mode: str,
) -> OverlayLifecycleConfiguration:
    overlay = settings.overlay
    desktop = overlay.desktop_flet
    width, height = resolve_desktop_overlay_size(desktop.size_preset)
    directive = OverlayOscDirective(
        "overlay_osc",
        overlay.target,
        overlay.show_translation,
        overlay.show_peer_original,
        settings.osc.host,
        settings.osc.port,
        settings.osc.chatbox_address,
        settings.osc.chatbox_send,
        settings.osc.chatbox_clear,
        settings.osc.chatbox_max_chars,
        settings.osc.vrc_mic_intercept,
        settings.osc.chatbox_include_source,
        overlay.calibration.copy(),
        {
            "size_preset": desktop.size_preset,
            "size": {"width": width, "height": height},
            "position": {"x": desktop.position.x, "y": desktop.position.y},
            "visual": {
                "text_scale": desktop.visual.text_scale,
                "background_alpha": desktop.visual.background_alpha,
                "outline_width": desktop.visual.outline_width,
            },
            "interaction_mode": interaction_mode,
        },
    )
    return OverlayLifecycleConfiguration(
        enabled,
        directive,
        settings.ui.locale,
        runtime_logging_mode,
        3000,
        0.05,
    )


@dataclass(slots=True)
class ProductionOverlayApplication:
    runtime: OverlayOscApplicationRuntime
    configuration: ResolvedOverlayConfiguration | None = None
    host: ClientHub | None = None
    _listeners: list[OverlayStateListener] = field(default_factory=list)
    _shutdown_started: bool = False
    _persist_desktop_bounds: Callable[[dict[str, int | float]], Awaitable[None]] | None = None
    _reset_desktop_position: Callable[[], Awaitable[None]] | None = None
    _pending_desktop_bounds: dict[str, int | float] | None = None
    _desktop_bounds_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        await self.runtime.startup()

    def bind_runtime_host(self, host: object) -> None:
        presenter = (
            self.runtime.handle.current_presenter_for_ingress()
            if self.runtime.handle is not None
            else None
        )
        diagnostics = self.runtime.handle.diagnostics if self.runtime.handle is not None else None
        if self.runtime.ingress is not None:
            self.runtime.ingress.detach_overlay_ingress(presenter)
        self.host = cast(ClientHub, host)
        self.runtime.ingress = HubOverlayIngress(cast(ClientHub, host))
        if presenter is not None:
            self.runtime.ingress.attach_overlay_ingress(presenter, diagnostics)

    def bind_desktop_operational_state(
        self,
        persist_bounds: Callable[[dict[str, int | float]], Awaitable[None]],
        reset_position: Callable[[], Awaitable[None]],
    ) -> None:
        self._persist_desktop_bounds = persist_bounds
        self._reset_desktop_position = reset_position

    def configure(self, configuration: OverlayLifecycleConfiguration) -> None:
        if self.configuration is None:
            self.configuration = ResolvedOverlayConfiguration(configuration)
            self.runtime.configuration = self.configuration
        else:
            self.configuration.replace(configuration)

    def configure_intent(
        self,
        settings: object,
        *,
        enabled: bool,
        runtime_logging_mode: str,
        interaction_mode: str,
    ) -> None:
        if isinstance(settings, AppSettingsVNext):
            configuration = resolve_overlay_lifecycle_configuration(settings, enabled=enabled)
        else:
            configuration = _resolve_legacy_overlay_lifecycle_configuration(
                settings,
                enabled=enabled,
                runtime_logging_mode=runtime_logging_mode,
                interaction_mode=interaction_mode,
            )
        self.configure(configuration)

    async def set_enabled(self, enabled: bool) -> None:
        if self.configuration is None:
            raise RuntimeError("overlay lifecycle configuration is unavailable")
        configured = self.configuration.value
        next_configuration = OverlayLifecycleConfiguration(
            enabled,
            configured.directive,
            configured.locale,
            configured.runtime_logging_mode,
            configured.startup_timeout_ms,
            configured.shutdown_grace_s,
        )
        self.configure(next_configuration)
        if enabled:
            await self.runtime.start_overlay(next_configuration)
        else:
            await self.runtime.shutdown(
                hub=self.host, preserve_failure_reason=self.runtime.failure_reason is not None
            )

    async def apply_configuration(self, configuration: OverlayLifecycleConfiguration) -> bool:
        self.configure(configuration)
        return await self.runtime.apply_overlay_osc(configuration.directive)

    async def apply_intent(
        self,
        settings: object,
        *,
        enabled: bool,
        runtime_logging_mode: str,
        interaction_mode: str,
    ) -> bool:
        previous_options = (
            self.configuration.value.directive.desktop_overlay_options
            if self.configuration is not None
            else {}
        )
        self.configure_intent(
            settings,
            enabled=enabled,
            runtime_logging_mode=runtime_logging_mode,
            interaction_mode=interaction_mode,
        )
        assert self.configuration is not None
        next_options = self.configuration.value.directive.desktop_overlay_options
        if previous_options.get("size_preset") != next_options.get("size_preset"):
            await self._cancel_desktop_bounds_persistence()
        return await self.runtime.apply_overlay_osc(self.configuration.value.directive)

    async def send_desktop_control(self, payload: Mapping[str, object]) -> bool:
        handle = self.runtime.handle
        bridge = handle.current_bridge_for_runtime_command() if handle is not None else None
        broadcast = getattr(bridge, "broadcast_desktop_runtime_control", None)
        if not callable(broadcast):
            return False
        await broadcast(dict(payload))
        if payload.get("command") == "set_interaction_mode":
            mode = payload.get("mode")
            if isinstance(mode, str):
                self.apply_desktop_interaction_mode_event(mode)
        return True

    def drain_pending_desktop_user_bounds_events(self) -> None:
        handle = self.runtime.handle
        events = handle.renderer_events if handle is not None else None
        if events is None:
            return
        retained: list[dict[str, object]] = []
        while True:
            try:
                event = events.get_nowait()
            except asyncio.QueueEmpty:
                break
            payload = event.get("payload")
            if not (
                isinstance(payload, Mapping)
                and payload.get("event") == "window_bounds_changed"
                and payload.get("source") == "user"
                and payload.get("persist") is True
            ):
                retained.append(event)
        for event in retained:
            events.put_nowait(event)

    async def persist_desktop_bounds(self, bounds: Mapping[str, int | float]) -> None:
        if self.runtime.interaction_mode != "edit" or self._persist_desktop_bounds is None:
            return
        self._pending_desktop_bounds = dict(bounds)
        if self._desktop_bounds_task is not None and not self._desktop_bounds_task.done():
            self._desktop_bounds_task.cancel()
        handle = self.runtime.handle
        if handle is None:
            self._pending_desktop_bounds = None
            return
        self._desktop_bounds_task = handle.create_child_task(
            self._flush_desktop_bounds(), task_name="persist-desktop-bounds"
        )

    async def _flush_desktop_bounds(self) -> None:
        try:
            await asyncio.sleep(0.05)
            bounds = self._pending_desktop_bounds
            self._pending_desktop_bounds = None
            if bounds is not None and self._persist_desktop_bounds is not None:
                await self._persist_desktop_bounds(bounds)
        finally:
            if self._desktop_bounds_task is asyncio.current_task():
                self._desktop_bounds_task = None

    async def reset_desktop_position(self) -> None:
        await self._cancel_desktop_bounds_persistence()
        if self._reset_desktop_position is not None:
            await self._reset_desktop_position()

    async def prepare_desktop_size_change(self) -> None:
        await self._cancel_desktop_bounds_persistence()

    async def _cancel_desktop_bounds_persistence(self) -> None:
        task = self._desktop_bounds_task
        self._desktop_bounds_task = None
        self._pending_desktop_bounds = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def apply_desktop_interaction_mode_event(self, mode: str) -> None:
        if mode in {"edit", "locked", "pass_through"}:
            self.runtime.interaction_mode = mode

    async def set_logging_mode(self, mode: str) -> None:
        handle = self.runtime.handle
        if handle is None:
            return
        manager = handle.process_manager
        update_manager = getattr(manager, "set_logging_mode", None)
        if callable(update_manager):
            update_manager(mode)
        bridge = handle.current_bridge_for_runtime_command()
        update_bridge = getattr(bridge, "broadcast_runtime_control", None)
        if callable(update_bridge):
            await update_bridge(logging_mode=mode)

    async def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        await self._cancel_desktop_bounds_persistence()
        await self.runtime.shutdown(hub=self.host)

    def lifecycle_snapshot(self) -> OverlayLifecycleSnapshot:
        handle = self.runtime.handle
        return OverlayLifecycleSnapshot(
            self.runtime.lifecycle_state,
            self.runtime.failure_reason,
            self.runtime.active_target,
            handle.overlay_instance_id if handle is not None else None,
        )

    def runtime_snapshot(self) -> OverlayOscRuntimeSnapshot:
        return self.runtime.snapshot()

    def subscribe(self, listener: OverlayStateListener) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self.lifecycle_snapshot())

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def publish_overlay_snapshot(self, snapshot: OverlayLifecycleSnapshot) -> None:
        for listener in tuple(self._listeners):
            listener(snapshot)


@dataclass(slots=True)
class HubOverlayIngress:
    hub: ClientHub
    _presenter: object | None = None

    def attach_overlay_ingress(self, presenter: object, diagnostics: object) -> None:
        if self._presenter is not None and self._presenter is not presenter:
            raise RuntimeError("overlay ingress already has an owner")
        self._presenter = presenter
        self.hub.overlay_sink = cast(Any, presenter)
        self.hub.overlay_diagnostics = cast(OverlayDiagnosticsRecorder, diagnostics)

    def detach_overlay_ingress(self, presenter: object | None) -> None:
        if presenter is not None and presenter is not self._presenter:
            return
        if getattr(self.hub, "overlay_sink", None) is self._presenter:
            self.hub.overlay_sink = None
        self.hub.overlay_diagnostics = None
        self._presenter = None


@dataclass(slots=True)
class ResolvedOverlayConfiguration:
    value: OverlayLifecycleConfiguration

    def resolved_overlay_configuration(self) -> OverlayLifecycleConfiguration:
        return self.value

    def replace(self, value: OverlayLifecycleConfiguration) -> None:
        self.value = value


@dataclass(slots=True)
class _ManagedProcess:
    manager: OverlayProcessManager

    @property
    def state(self) -> str:
        return self.manager.state

    @property
    def failure_reason(self) -> str | None:
        return self.manager.failure_reason

    @property
    def monitor_task(self) -> asyncio.Task[None] | None:
        return self.manager.monitor_task

    async def start(self) -> None:
        await self.manager.start()

    async def stop(self) -> None:
        await self.manager.stop()


RunnerFactory = Callable[[str, Callable[..., asyncio.Task[object]]], OverlayProcessRunner]


@dataclass(slots=True)
class ProductionOverlayLifecycleFactories:
    runner_factory: RunnerFactory | None = None
    desktop_work_area_provider: (
        Callable[[], tuple[int | float, int | float, int | float, int | float] | None] | None
    ) = None

    def create_diagnostics(self, *, overlay_instance_id: str) -> object:
        return OverlayDiagnosticsRecorder(overlay_instance_id=overlay_instance_id)

    def create_presenter(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        diagnostics: object,
        task_factory: Callable[..., asyncio.Task[object]],
    ) -> OverlayPresenter:
        is_desktop = configuration.directive.overlay_target == "desktop"
        if configuration.runtime_logging_mode == "detailed":
            logger.info(
                "[Overlay][Start] target=%s logging_mode=detailed "
                "peer_presentation_refresh_burst=%s self_presentation_refresh_burst=%s",
                configuration.directive.overlay_target,
                not is_desktop,
                not is_desktop,
            )
        return OverlayPresenter(
            calibration=configuration.directive.calibration.copy(),
            diagnostics=cast(OverlayDiagnosticsRecorder, diagnostics),
            runtime_log_detailed=(
                _detailed_overlay_log if configuration.runtime_logging_mode == "detailed" else None
            ),
            show_translation=configuration.directive.show_translation,
            show_peer_original=configuration.directive.show_peer_original,
            peer_presentation_refresh_burst=not is_desktop,
            self_presentation_refresh_burst=not is_desktop,
            task_factory=task_factory,
        )

    def create_bridge(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        presenter_snapshot: object,
        diagnostics: object,
        overlay_instance_id: str,
        session_token: str,
        task_factory: Callable[..., asyncio.Task[object]],
    ) -> OverlayBridge:
        is_desktop = configuration.directive.overlay_target == "desktop"
        bridge = OverlayBridge(
            session_token=session_token,
            initial_snapshot=cast(Any, presenter_snapshot),
            overlay_instance_id=overlay_instance_id,
            diagnostics=cast(OverlayDiagnosticsRecorder, diagnostics),
            runtime_logging_mode=configuration.runtime_logging_mode,
            desktop_runtime_controls_enabled=is_desktop,
            task_factory=task_factory,
        )
        if is_desktop:
            options = configuration.directive.desktop_overlay_options
            controls = _initial_desktop_controls(
                options,
                work_area=(
                    self.desktop_work_area_provider()
                    if self.desktop_work_area_provider is not None
                    else None
                ),
            )
            bridge.set_initial_desktop_runtime_controls(controls)
            if configuration.runtime_logging_mode == "detailed":
                bounds = controls[0]
                logger.info(
                    "[DesktopOverlay][Launch] target=desktop locked=%s interaction_mode=%s "
                    "size_preset=%s x=%s y=%s width=%s height=%s background_alpha=%s",
                    options.get("locked"),
                    options.get("interaction_mode"),
                    options.get("size_preset"),
                    bounds.get("x"),
                    bounds.get("y"),
                    bounds.get("width"),
                    bounds.get("height"),
                    _mapping(options.get("visual")).get("background_alpha"),
                )
        return bridge

    def create_process_manager(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        bridge: OverlayBridgePort,
        diagnostics: object,
        renderer_events: asyncio.Queue[dict[str, object]] | None,
        overlay_instance_id: str,
        task_factory: Callable[..., asyncio.Task[object]],
    ) -> _ManagedProcess:
        target = configuration.directive.overlay_target
        runner = (
            self.runner_factory(target, task_factory)
            if self.runner_factory is not None
            else _default_runner(target, task_factory)
        )
        return _ManagedProcess(
            OverlayProcessManager(
                process_runner=runner,
                startup_timeout_ms=configuration.startup_timeout_ms,
                bridge_url=bridge.url,
                bridge_messages=cast(Any, bridge.messages),
                session_token=bridge.session_token,
                locale=configuration.locale,
                logging_mode=configuration.runtime_logging_mode,
                renderer_events=renderer_events,
                overlay_instance_id=overlay_instance_id,
                diagnostics=cast(OverlayDiagnosticsRecorder, diagnostics),
            )
        )


def _default_runner(
    target: str, task_factory: Callable[..., asyncio.Task[object]]
) -> OverlayProcessRunner:
    if target == "desktop":
        return DesktopFletOverlayRunner(task_factory=task_factory)
    return DefaultOverlayProcessRunner(task_factory=task_factory)


def _detailed_overlay_log(message: str, *, level: int = logging.INFO) -> bool:
    logger.log(level, message)
    return True


def _initial_desktop_controls(
    options: Mapping[str, object],
    *,
    work_area: tuple[int | float, int | float, int | float, int | float] | None = None,
) -> list[dict[str, object]]:
    position = _mapping(options.get("position"))
    size = _mapping(options.get("size"))
    visual = _mapping(options.get("visual"))
    x = position.get("x")
    y = position.get("y")
    width = size.get("width")
    height = size.get("height")
    if x is None and y is None and work_area is not None:
        left, top, work_width, work_height = work_area
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            x = left + (work_width - width) / 2
            y = top + (work_height - height) / 2
    controls = [
        {
            "command": "apply_window_bounds",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        },
        {
            "command": "apply_visual_config",
            "text_scale": visual.get("text_scale"),
            "background_alpha": visual.get("background_alpha"),
            "outline_width": visual.get("outline_width"),
        },
    ]
    mode = options.get("interaction_mode")
    if isinstance(mode, str):
        controls.append({"command": "set_interaction_mode", "mode": mode})
    return controls


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
