from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from puripuly_heart.app.ports.overlay_application import (
    OverlayLifecycleConfiguration,
    OverlayLifecycleSnapshot,
    OverlayLifecycleState,
    OverlayOscRuntimeSnapshot,
)
from puripuly_heart.app.ports.post_commit_runtime import (
    DashboardRetryFactsDirective,
    OverlayOscDirective,
)
from puripuly_heart.config.overlay_calibration import OverlayCalibration
from puripuly_heart.core.runtime.overlay import OverlayRuntimeHandle


class DashboardRuntimeFactsConsumer(Protocol):
    def publish_dashboard_runtime_facts(self, facts: DashboardRetryFactsDirective) -> None: ...


class VrcMicrophoneEffectsPort(Protocol):
    async def apply_vrc_microphone_intercept(self, enabled: bool) -> bool: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OverlayLogFacts:
    event: str
    target: str | None = None
    overlay_instance_id: str | None = None
    failure_reason: str | None = None


class OverlayConfigurationPort(Protocol):
    def resolved_overlay_configuration(self) -> OverlayLifecycleConfiguration: ...


class OverlayIngressPort(Protocol):
    def attach_overlay_ingress(self, presenter: object, diagnostics: object) -> None: ...

    def detach_overlay_ingress(self, presenter: object | None) -> None: ...


class OverlayLifecycleOutputPort(Protocol):
    def publish_overlay_snapshot(self, snapshot: OverlayLifecycleSnapshot) -> None: ...


class OverlayRendererEventPort(Protocol):
    async def handle_renderer_event(
        self, event: Mapping[str, object], *, overlay_instance_id: str
    ) -> None: ...


class OverlaySafeLogPort(Protocol):
    def publish_overlay_log_facts(self, facts: OverlayLogFacts) -> None: ...


class OverlayPresenterPort(Protocol):
    def snapshot(self) -> object: ...

    def attach_bridge(self, bridge: object) -> None: ...

    async def update_calibration(self, calibration: OverlayCalibration) -> None: ...

    async def update_display_preferences(
        self, *, show_translation: bool, show_peer_original: bool
    ) -> None: ...


class OverlayBridgePort(Protocol):
    url: str
    messages: object
    session_token: str

    async def start(self) -> None: ...

    def snapshot(self) -> object: ...

    async def replace_snapshot(self, snapshot: object) -> None: ...

    async def update_native_retry_ownership(self, confirmed: bool) -> None: ...


class OverlayProcessManagerPort(Protocol):
    state: str
    failure_reason: str | None
    monitor_task: asyncio.Task[None] | None

    async def start(self) -> None: ...


async def _apply_retry_ownership_change(
    presenter: object,
    confirmed: bool,
    refresh_burst: bool,
) -> None:
    update_ownership = getattr(presenter, "update_native_retry_ownership", None)
    if callable(update_ownership):
        await update_ownership(confirmed)
    if not refresh_burst:
        update_peer = getattr(presenter, "update_peer_presentation_refresh_burst", None)
        update_self = getattr(presenter, "update_self_presentation_refresh_burst", None)
        if callable(update_peer):
            await update_peer(False)
        if callable(update_self):
            await update_self(False)


class OverlayLifecycleFactories(Protocol):
    def create_diagnostics(self, *, overlay_instance_id: str) -> object: ...

    def create_presenter(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        diagnostics: object,
        task_factory: Callable[..., asyncio.Task[object]],
    ) -> OverlayPresenterPort: ...

    def create_bridge(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        presenter_snapshot: object,
        diagnostics: object,
        overlay_instance_id: str,
        session_token: str,
        task_factory: Callable[..., asyncio.Task[object]],
    ) -> OverlayBridgePort: ...

    def create_process_manager(
        self,
        *,
        configuration: OverlayLifecycleConfiguration,
        bridge: OverlayBridgePort,
        diagnostics: object,
        renderer_events: asyncio.Queue[dict[str, object]] | None,
        overlay_instance_id: str,
        task_factory: Callable[..., asyncio.Task[object]],
        retry_ownership_changed: Callable[[bool], Awaitable[None]],
    ) -> OverlayProcessManagerPort: ...


@dataclass(slots=True)
class OverlayOscApplicationRuntime:
    dashboard: DashboardRuntimeFactsConsumer | None = None
    vrc_microphone: VrcMicrophoneEffectsPort | None = None
    handle: OverlayRuntimeHandle | None = None
    calibration: OverlayCalibration = field(default_factory=OverlayCalibration)
    calibration_draft: OverlayCalibration | None = None
    interaction_mode: str = "edit"
    directive: OverlayOscDirective | None = None
    dashboard_facts: DashboardRetryFactsDirective | None = None
    started: bool = False
    closed: bool = False
    configuration: OverlayConfigurationPort | None = None
    ingress: OverlayIngressPort | None = None
    factories: OverlayLifecycleFactories | None = None
    lifecycle_output: OverlayLifecycleOutputPort | None = None
    renderer_output: OverlayRendererEventPort | None = None
    safe_log: OverlaySafeLogPort | None = None
    lifecycle_state: OverlayLifecycleState = "off"
    failure_reason: str | None = None
    active_target: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _preserved_presenter: object | None = field(default=None, init=False, repr=False)
    _vrc_microphone_closed: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        self.started = True
        self.closed = False
        if self.configuration is not None:
            configuration = self.configuration.resolved_overlay_configuration()
            self.directive = configuration.directive
            if self.vrc_microphone is not None:
                applied = await self.vrc_microphone.apply_vrc_microphone_intercept(
                    configuration.directive.vrc_mic_intercept
                )
                if not applied:
                    self._set_lifecycle("failed", failure_reason="unknown", active_target=None)
                    return
            if configuration.enabled:
                await self.start_overlay(configuration)

    async def shutdown(
        self, *, hub: object | None = None, preserve_failure_reason: bool = False
    ) -> None:
        async with self._lock:
            previous_failure_reason = self.failure_reason
            close_failed = False
            try:
                await self._close_current(
                    preserve_presenter_state=False, emit_shutdown=True, hub=hub
                )
            except Exception:
                close_failed = True
            try:
                await self._close_vrc_microphone()
            except Exception:
                close_failed = True
            if close_failed:
                self._set_lifecycle(
                    "failed", failure_reason="unknown", active_target=self.active_target
                )
                return
            reset_preview = getattr(hub, "reset_overlay_preview", None)
            if callable(reset_preview):
                await reset_preview()
            self._set_lifecycle(
                "off",
                failure_reason=(previous_failure_reason if preserve_failure_reason else None),
                active_target=None,
            )
            self.closed = True

    async def _close_vrc_microphone(self) -> None:
        if self._vrc_microphone_closed or self.vrc_microphone is None:
            return
        self._vrc_microphone_closed = True
        await self.vrc_microphone.close()

    def complete_shutdown(self) -> None:
        self.handle = None
        self.closed = True

    def ensure_handle(self, *, shutdown_grace_s: float) -> OverlayRuntimeHandle:
        if self.handle is None:
            self.handle = OverlayRuntimeHandle(shutdown_grace_s=shutdown_grace_s)
        return self.handle

    async def apply_overlay_osc(self, directive: OverlayOscDirective) -> bool:
        previous = self.directive
        if (
            previous is not None
            and previous.overlay_target != directive.overlay_target
            and self.factories is not None
            and self.configuration is not None
        ):
            configured = self.configuration.resolved_overlay_configuration()
            replacement = OverlayLifecycleConfiguration(
                configured.enabled,
                directive,
                configured.locale,
                configured.runtime_logging_mode,
                configured.startup_timeout_ms,
                configured.shutdown_grace_s,
            )
            await self.switch_target(replacement)
        presenter = self.handle.current_presenter_for_ingress() if self.handle else None
        if presenter is not None:
            await presenter.update_display_preferences(
                show_translation=directive.show_translation,
                show_peer_original=directive.show_peer_original,
            )
            await presenter.update_calibration(directive.calibration.copy())
        if previous is not None and not await self._apply_desktop_runtime_controls(
            previous, directive
        ):
            return False
        if previous is None or previous.vrc_mic_intercept != directive.vrc_mic_intercept:
            if (
                self.vrc_microphone is not None
                and not await self.vrc_microphone.apply_vrc_microphone_intercept(
                    directive.vrc_mic_intercept
                )
            ):
                return False
        self.directive = directive
        self.calibration = directive.calibration.copy()
        return True

    async def start_overlay(self, configuration: OverlayLifecycleConfiguration) -> None:
        if self.factories is None or self.ingress is None:
            raise RuntimeError("overlay lifecycle dependencies are unavailable")
        async with self._lock:
            if self.lifecycle_state in {"starting", "connected"}:
                return
            try:
                preserved = await self._close_current(
                    preserve_presenter_state=True, emit_shutdown=False, hub=None
                )
            except Exception:
                self._set_lifecycle(
                    "failed", failure_reason="unknown", active_target=self.active_target
                )
                return
            if preserved is None:
                preserved = self._preserved_presenter
            self._preserved_presenter = None
            handle = OverlayRuntimeHandle(shutdown_grace_s=configuration.shutdown_grace_s)
            if preserved is not None:
                handle.adopt_presenter(preserved)
            self.handle = handle
            self.directive = configuration.directive
            self._set_lifecycle(
                "starting",
                failure_reason=None,
                active_target=configuration.directive.overlay_target,
            )
            handle.create_start_task(self._run_start(handle, configuration))

    async def switch_target(self, configuration: OverlayLifecycleConfiguration) -> None:
        async with self._lock:
            self._preserved_presenter = await self._close_current(
                preserve_presenter_state=True, emit_shutdown=False, hub=None
            )
            self.lifecycle_state = "off"
        await self.start_overlay(configuration)

    async def _run_start(
        self, handle: OverlayRuntimeHandle, configuration: OverlayLifecycleConfiguration
    ) -> None:
        assert self.factories is not None
        assert self.ingress is not None
        instance_id = f"overlay-{secrets.token_hex(8)}"
        handle.set_overlay_instance_id(instance_id)
        try:
            diagnostics = self.factories.create_diagnostics(overlay_instance_id=instance_id)
            handle.attach_diagnostics(diagnostics)
            presenter = cast(OverlayPresenterPort | None, handle.presenter)
            if presenter is None:
                presenter = self.factories.create_presenter(
                    configuration=configuration,
                    diagnostics=diagnostics,
                    task_factory=handle.create_child_task,
                )
            presenter = cast(OverlayPresenterPort, handle.adopt_presenter(presenter))
            refresh_burst = configuration.directive.overlay_target != "desktop"
            update_peer_burst = getattr(
                presenter,
                "update_peer_presentation_refresh_burst",
                None,
            )
            update_self_burst = getattr(
                presenter,
                "update_self_presentation_refresh_burst",
                None,
            )
            if callable(update_peer_burst):
                await update_peer_burst(refresh_burst)
            elif hasattr(presenter, "peer_presentation_refresh_burst"):
                presenter.peer_presentation_refresh_burst = refresh_burst
            if callable(update_self_burst):
                await update_self_burst(refresh_burst)
            elif hasattr(presenter, "self_presentation_refresh_burst"):
                presenter.self_presentation_refresh_burst = refresh_burst
            await presenter.update_calibration(configuration.directive.calibration.copy())
            await presenter.update_display_preferences(
                show_translation=configuration.directive.show_translation,
                show_peer_original=configuration.directive.show_peer_original,
            )
            bridge = self.factories.create_bridge(
                configuration=configuration,
                presenter_snapshot=presenter.snapshot(),
                diagnostics=diagnostics,
                overlay_instance_id=instance_id,
                session_token=secrets.token_urlsafe(16),
                task_factory=handle.create_child_task,
            )
            handle.attach_bridge(bridge)
            await bridge.start()
            if not self._is_current(handle, instance_id):
                await handle.close(preserve_presenter_state=True, hub=None, emit_shutdown=False)
                return
            presenter.attach_bridge(bridge)
            snapshot = presenter.snapshot()
            if bridge.snapshot() != snapshot:
                await bridge.replace_snapshot(snapshot)
            self.ingress.attach_overlay_ingress(presenter, diagnostics)
            renderer_events = None
            if configuration.directive.overlay_target == "desktop":
                if self.renderer_output is None:
                    raise RuntimeError("desktop renderer event consumer is unavailable")
                renderer_events = asyncio.Queue(maxsize=64)
                handle.attach_renderer_events(renderer_events)
                handle.create_renderer_event_task(
                    self._consume_renderer_events(renderer_events, handle, instance_id)
                )
            manager = self.factories.create_process_manager(
                configuration=configuration,
                bridge=bridge,
                diagnostics=diagnostics,
                renderer_events=renderer_events,
                overlay_instance_id=instance_id,
                task_factory=handle.create_child_task,
                retry_ownership_changed=lambda confirmed: _apply_retry_ownership_change(
                    presenter,
                    confirmed,
                    refresh_burst,
                ),
            )
            handle.attach_process_manager(manager)
            await manager.start()
            if not self._is_current(handle, instance_id):
                await handle.close(preserve_presenter_state=True, hub=None, emit_shutdown=False)
                return
            if manager.state != "connected":
                await self._fail_current(handle, manager.failure_reason)
                return
            self._set_lifecycle("connected", failure_reason=None, active_target=self.active_target)
            if manager.monitor_task is not None:
                handle.create_monitor_task(
                    self._watch_runtime(handle, manager, manager.monitor_task, instance_id)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._is_current(handle, instance_id):
                await self._fail_current(handle, "unknown")
            else:
                await handle.close(preserve_presenter_state=True, hub=None, emit_shutdown=False)

    async def _watch_runtime(
        self,
        handle: OverlayRuntimeHandle,
        manager: OverlayProcessManagerPort,
        monitor_task: asyncio.Task[None],
        instance_id: str,
    ) -> None:
        await monitor_task
        if self._is_current(handle, instance_id) and manager.state == "failed":
            await self._fail_current(handle, manager.failure_reason)

    async def _consume_renderer_events(
        self,
        events: asyncio.Queue[dict[str, object]],
        handle: OverlayRuntimeHandle,
        instance_id: str,
    ) -> None:
        assert self.renderer_output is not None
        while True:
            event = await events.get()
            if self._is_current(handle, instance_id):
                await self.renderer_output.handle_renderer_event(
                    event, overlay_instance_id=instance_id
                )

    async def _fail_current(self, handle: OverlayRuntimeHandle, failure_reason: str | None) -> None:
        if self.handle is not handle:
            return
        reason = failure_reason if isinstance(failure_reason, str) and failure_reason else "unknown"
        self._set_lifecycle("failed", failure_reason=reason, active_target=self.active_target)
        await self._close_current(preserve_presenter_state=True, emit_shutdown=False, hub=None)

    async def _close_current(
        self,
        *,
        preserve_presenter_state: bool,
        emit_shutdown: bool,
        hub: object | None,
    ) -> object | None:
        handle = self.handle
        if handle is None:
            return None
        presenter = handle.presenter
        if self.ingress is not None:
            self.ingress.detach_overlay_ingress(presenter)
        try:
            await handle.close(
                preserve_presenter_state=preserve_presenter_state,
                hub=hub,
                emit_shutdown=emit_shutdown,
            )
        except Exception:
            if self.safe_log is not None:
                self.safe_log.publish_overlay_log_facts(
                    OverlayLogFacts(
                        "cleanup_failed",
                        self.active_target,
                        handle.overlay_instance_id,
                        None,
                    )
                )
            raise
        preserved = handle.detach_preserved_presenter() if preserve_presenter_state else None
        if self.handle is handle:
            self.handle = None
        return preserved

    def _is_current(self, handle: OverlayRuntimeHandle, instance_id: str) -> bool:
        return self.handle is handle and handle.is_current_instance_id(instance_id)

    def _set_lifecycle(
        self,
        state: OverlayLifecycleState,
        *,
        failure_reason: str | None,
        active_target: str | None,
    ) -> None:
        self.lifecycle_state = state
        self.failure_reason = failure_reason
        self.active_target = active_target
        instance_id = self.handle.overlay_instance_id if self.handle is not None else None
        snapshot = OverlayLifecycleSnapshot(state, failure_reason, active_target, instance_id)
        if self.lifecycle_output is not None:
            self.lifecycle_output.publish_overlay_snapshot(snapshot)
        if self.safe_log is not None:
            self.safe_log.publish_overlay_log_facts(
                OverlayLogFacts("state_changed", active_target, instance_id, failure_reason)
            )

    async def _apply_desktop_runtime_controls(
        self,
        previous: OverlayOscDirective,
        directive: OverlayOscDirective,
    ) -> bool:
        if directive.overlay_target != "desktop":
            return True
        bridge = self.handle.current_bridge_for_runtime_command() if self.handle else None
        if bridge is None:
            return True
        broadcast = getattr(bridge, "broadcast_desktop_runtime_control", None)
        if not callable(broadcast):
            return False
        options = directive.desktop_overlay_options
        position = _mapping(options.get("position"))
        visual = _mapping(options.get("visual"))
        size = options.get("size")
        if not isinstance(size, Mapping):
            size = {}
        controls: list[dict[str, object]] = []
        previous_options = previous.desktop_overlay_options
        if previous_options.get("size") != options.get("size") or previous_options.get(
            "position"
        ) != options.get("position"):
            controls.append(
                {
                    "command": "apply_window_bounds",
                    "x": position.get("x"),
                    "y": position.get("y"),
                    "width": size.get("width"),
                    "height": size.get("height"),
                }
            )
        if previous_options.get("visual") != options.get("visual"):
            controls.append(
                {
                    "command": "apply_visual_config",
                    "text_scale": visual.get("text_scale"),
                    "background_alpha": visual.get("background_alpha"),
                    "outline_width": visual.get("outline_width"),
                }
            )
        for control in controls:
            if all(value is None for key, value in control.items() if key != "command"):
                continue
            await broadcast(control)
        mode = options.get("interaction_mode")
        if isinstance(mode, str) and mode != previous_options.get("interaction_mode"):
            await broadcast({"command": "set_interaction_mode", "mode": mode})
            self.interaction_mode = mode
        return True

    async def publish_dashboard_retry_facts(self, directive: DashboardRetryFactsDirective) -> bool:
        self.dashboard_facts = directive
        if self.dashboard is None:
            return False
        self.dashboard.publish_dashboard_runtime_facts(directive)
        return True

    def snapshot(self) -> OverlayOscRuntimeSnapshot:
        return OverlayOscRuntimeSnapshot(
            self.directive,
            self.dashboard_facts,
            self.calibration.copy(),
            self.interaction_mode,
        )


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}
