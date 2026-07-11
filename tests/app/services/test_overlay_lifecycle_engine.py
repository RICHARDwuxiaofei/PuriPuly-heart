from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.app.adapters.overlay_lifecycle_production import ProductionOverlayApplication
from puripuly_heart.app.ports.post_commit_runtime import OverlayOscDirective
from puripuly_heart.app.services.overlay_osc_application_runtime import (
    OverlayLifecycleConfiguration,
    OverlayLogFacts,
    OverlayOscApplicationRuntime,
)
from puripuly_heart.core.runtime.overlay import OverlayRuntimeHandle


def directive(target: str = "steamvr") -> OverlayOscDirective:
    return OverlayOscDirective(
        "overlay_osc",
        target,
        True,
        False,
        "127.0.0.1",
        9000,
        "/chatbox/input",
        True,
        True,
        144,
        False,
        False,
    )


def configuration(target: str = "steamvr") -> OverlayLifecycleConfiguration:
    return OverlayLifecycleConfiguration(True, directive(target), "en", "basic", 5_000, 0)


class Ingress:
    def __init__(self) -> None:
        self.presenter = None
        self.attachments = 0

    def attach_overlay_ingress(self, presenter: object, diagnostics: object) -> None:
        self.presenter = presenter
        self.attachments += 1

    def detach_overlay_ingress(self, presenter: object | None) -> None:
        if self.presenter is presenter:
            self.presenter = None


class Presenter:
    diagnostics = None
    task_factory = None

    def __init__(self) -> None:
        self.bridge = None
        self.closed = 0

    def snapshot(self) -> object:
        return {"ready": True}

    def attach_bridge(self, bridge: object) -> None:
        self.bridge = bridge

    def detach_bridge(self) -> None:
        self.bridge = None

    async def update_calibration(self, calibration: object) -> None:
        return None

    async def update_display_preferences(self, **preferences: bool) -> None:
        return None

    async def broadcast_shutdown(self) -> None:
        return None

    async def close(self) -> None:
        self.closed += 1

    def reset_scene(self) -> None:
        return None


class Bridge:
    url = "ws://overlay"
    messages = object()
    session_token = "token"

    def __init__(self, start_gate: asyncio.Event | None = None) -> None:
        self.start_gate = start_gate
        self.stops = 0

    async def start(self) -> None:
        if self.start_gate is not None:
            await self.start_gate.wait()

    def snapshot(self) -> object:
        return {"ready": True}

    async def replace_snapshot(self, snapshot: object) -> None:
        return None

    async def stop(self) -> None:
        self.stops += 1


class Manager:
    def __init__(self, state: str = "connected", failure_reason: str | None = None) -> None:
        self.state = state
        self.failure_reason = failure_reason
        self.monitor_task: asyncio.Task[None] | None = None
        self.stops = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stops += 1


class Factories:
    def __init__(
        self, *, manager: Manager | None = None, gate: asyncio.Event | None = None
    ) -> None:
        self.manager = manager or Manager()
        self.gate = gate
        self.presenters: list[Presenter] = []
        self.bridges: list[Bridge] = []

    def create_diagnostics(self, **values: object) -> object:
        return object()

    def create_presenter(self, **values: object) -> Presenter:
        presenter = Presenter()
        self.presenters.append(presenter)
        return presenter

    def create_bridge(self, **values: object) -> Bridge:
        bridge = Bridge(self.gate)
        self.bridges.append(bridge)
        return bridge

    def create_process_manager(self, **values: object) -> Manager:
        return self.manager


async def settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_successful_start_and_one_close_shutdown() -> None:
    ingress = Ingress()
    factories = Factories()
    runtime = OverlayOscApplicationRuntime(ingress=ingress, factories=factories)
    await runtime.start_overlay(configuration())
    await settle()
    assert runtime.lifecycle_state == "connected"
    assert ingress.presenter is factories.presenters[0]
    await asyncio.gather(runtime.shutdown(), runtime.shutdown())
    assert runtime.lifecycle_state == "off"
    assert factories.presenters[0].closed == 1
    assert factories.manager.stops == 1
    assert factories.bridges[0].stops == 1


@pytest.mark.asyncio
async def test_start_failure_closes_generation_and_preserves_presenter() -> None:
    factories = Factories(manager=Manager("failed", "startup_timeout"))
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration())
    await settle()
    assert runtime.lifecycle_state == "failed"
    assert runtime.failure_reason == "startup_timeout"
    assert runtime.handle is None
    assert factories.presenters[0].closed == 0


@pytest.mark.asyncio
async def test_target_switch_reuses_presenter_and_closes_old_resources() -> None:
    factories = Factories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration())
    await settle()
    first_handle = runtime.handle
    await runtime.switch_target(configuration("desktop"))
    await settle()
    assert first_handle is not runtime.handle
    assert runtime.active_target == "desktop"
    assert len(factories.presenters) == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stale_generation_cannot_publish_connected() -> None:
    gate = asyncio.Event()
    factories = Factories(gate=gate)
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration())
    stale_handle = runtime.handle
    await runtime.switch_target(configuration("desktop"))
    gate.set()
    await settle()
    assert stale_handle is not runtime.handle
    assert runtime.active_target == "desktop"
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_disconnect_fails_and_tears_down() -> None:
    manager = Manager()
    factories = Factories(manager=manager)
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    disconnected = asyncio.Event()
    manager.monitor_task = asyncio.create_task(disconnected.wait())
    await runtime.start_overlay(configuration())
    await settle()
    manager.state = "failed"
    manager.failure_reason = "runtime_disconnected"
    disconnected.set()
    await settle()
    assert runtime.lifecycle_state == "failed"
    assert runtime.failure_reason == "runtime_disconnected"


@pytest.mark.asyncio
async def test_shutdown_cancels_in_progress_start() -> None:
    factories = Factories(gate=asyncio.Event())
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration())
    task = runtime.handle.start_task if runtime.handle else None
    await runtime.shutdown()
    assert task is not None and task.cancelled()
    assert runtime.lifecycle_state == "off"


@pytest.mark.asyncio
async def test_closing_desktop_overlay_runtime_rejects_direct_bridge_commands() -> None:
    class BlockingBridge(Bridge):
        def __init__(self) -> None:
            super().__init__()
            self.shutdown_entered = asyncio.Event()
            self.shutdown_released = asyncio.Event()
            self.controls: list[dict[str, object]] = []

        async def broadcast_shutdown(self) -> None:
            self.shutdown_entered.set()
            await self.shutdown_released.wait()

        async def broadcast_desktop_runtime_control(self, payload: dict[str, object]) -> None:
            self.controls.append(payload)

    bridge = BlockingBridge()
    handle = OverlayRuntimeHandle(shutdown_grace_s=0)
    handle.attach_bridge(bridge)
    runtime = OverlayOscApplicationRuntime(handle=handle, lifecycle_state="connected")
    application = ProductionOverlayApplication(runtime)
    close_task = asyncio.create_task(
        handle.close(preserve_presenter_state=False, hub=None, emit_shutdown=True)
    )
    await bridge.shutdown_entered.wait()
    try:
        assert await application.send_desktop_control({"command": "set_interaction_mode"}) is False
        assert bridge.controls == []
    finally:
        bridge.shutdown_released.set()
        await close_task


@pytest.mark.asyncio
async def test_closing_overlay_runtime_rejects_direct_presenter_commands() -> None:
    class BlockingPresenter(Presenter):
        def __init__(self) -> None:
            super().__init__()
            self.shutdown_entered = asyncio.Event()
            self.shutdown_released = asyncio.Event()
            self.preference_updates = 0

        async def broadcast_shutdown(self) -> None:
            self.shutdown_entered.set()
            await self.shutdown_released.wait()

        async def update_display_preferences(self, **preferences: bool) -> None:
            self.preference_updates += 1

    presenter = BlockingPresenter()
    handle = OverlayRuntimeHandle(shutdown_grace_s=0)
    handle.attach_presenter(presenter)
    runtime = OverlayOscApplicationRuntime(
        handle=handle,
        lifecycle_state="connected",
        directive=directive(),
    )
    close_task = asyncio.create_task(
        handle.close(preserve_presenter_state=True, hub=None, emit_shutdown=True)
    )
    await presenter.shutdown_entered.wait()
    try:
        await runtime.apply_overlay_osc(directive("desktop"))
        assert presenter.preference_updates == 0
    finally:
        presenter.shutdown_released.set()
        await close_task


@pytest.mark.asyncio
async def test_overlay_teardown_close_failure_falls_back_to_basic_runtime_log() -> None:
    class FailingManager(Manager):
        async def stop(self) -> None:
            raise RuntimeError("raw cleanup failure details must stay internal")

    class SafeLog:
        def __init__(self) -> None:
            self.facts: list[OverlayLogFacts] = []

        def publish_overlay_log_facts(self, facts: OverlayLogFacts) -> None:
            self.facts.append(facts)

    handle = OverlayRuntimeHandle(shutdown_grace_s=0)
    handle.attach_process_manager(FailingManager())
    safe_log = SafeLog()
    runtime = OverlayOscApplicationRuntime(handle=handle, safe_log=safe_log)

    await runtime.shutdown()

    assert runtime.lifecycle_state == "failed"
    assert [facts.event for facts in safe_log.facts] == ["cleanup_failed", "state_changed"]
    assert "raw cleanup failure" not in repr(safe_log.facts)


@pytest.mark.asyncio
async def test_overlay_shutdown_keeps_failed_state_when_cleanup_fails_with_resources() -> None:
    class FailingManager(Manager):
        async def stop(self) -> None:
            self.stops += 1
            raise RuntimeError("manager still needs retry")

    manager = FailingManager()
    handle = OverlayRuntimeHandle(shutdown_grace_s=0)
    handle.attach_process_manager(manager)
    runtime = OverlayOscApplicationRuntime(
        handle=handle,
        lifecycle_state="connected",
        failure_reason="runtime_crashed",
        active_target="steamvr",
    )

    await runtime.shutdown()

    assert manager.stops == 1
    assert runtime.lifecycle_state == "failed"
    assert runtime.failure_reason == "unknown"
    assert runtime.handle is handle
    assert handle.process_manager is manager


@pytest.mark.asyncio
async def test_overlay_restart_aborts_when_preserve_teardown_close_fails() -> None:
    class FailingManager(Manager):
        async def stop(self) -> None:
            raise RuntimeError("manager still needs retry")

    factories = Factories()
    manager = FailingManager()
    handle = OverlayRuntimeHandle(shutdown_grace_s=0)
    handle.attach_process_manager(manager)
    runtime = OverlayOscApplicationRuntime(
        handle=handle,
        lifecycle_state="failed",
        ingress=Ingress(),
        factories=factories,
    )

    await runtime.start_overlay(configuration())

    assert runtime.handle is handle
    assert handle.process_manager is manager
    assert handle.start_task is None
    assert runtime.lifecycle_state == "failed"
    assert factories.presenters == []


@pytest.mark.asyncio
async def test_stale_desktop_renderer_event_is_ignored_after_overlay_instance_change() -> None:
    class RendererOutput:
        def __init__(self) -> None:
            self.events: list[object] = []

        async def handle_renderer_event(self, event, *, overlay_instance_id: str) -> None:
            self.events.append((event, overlay_instance_id))

    output = RendererOutput()
    stale = OverlayRuntimeHandle(overlay_instance_id="overlay-old")
    current = OverlayRuntimeHandle(overlay_instance_id="overlay-new")
    events: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    runtime = OverlayOscApplicationRuntime(handle=current, renderer_output=output)
    task = asyncio.create_task(runtime._consume_renderer_events(events, stale, "overlay-old"))
    try:
        await events.put({"type": "overlay_event", "payload": {"event": "window_bounds_changed"}})
        await settle()
        assert output.events == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_desktop_apply_settings_broadcasts_visual_config_for_background_alpha_change() -> (
    None
):
    class ControlBridge(Bridge):
        def __init__(self) -> None:
            super().__init__()
            self.controls: list[dict[str, object]] = []

        async def broadcast_desktop_runtime_control(self, payload: dict[str, object]) -> None:
            self.controls.append(payload)

    previous = directive("desktop")
    previous.desktop_overlay_options["visual"] = {"background_alpha": 0.5}
    updated = directive("desktop")
    updated.desktop_overlay_options["visual"] = {
        "text_scale": 1.0,
        "background_alpha": 0.7,
        "outline_width": None,
    }
    bridge = ControlBridge()
    handle = OverlayRuntimeHandle()
    handle.attach_bridge(bridge)
    runtime = OverlayOscApplicationRuntime(handle=handle, directive=previous)

    assert await runtime.apply_overlay_osc(updated) is True
    assert bridge.controls == [
        {
            "command": "apply_visual_config",
            "text_scale": 1.0,
            "background_alpha": 0.7,
            "outline_width": None,
        }
    ]


async def _assert_target_switch_stops_running_target() -> None:
    class SwitchingFactories(Factories):
        def __init__(self) -> None:
            super().__init__()
            self.managers: list[Manager] = []

        def create_process_manager(self, **values: object) -> Manager:
            manager = Manager()
            self.managers.append(manager)
            return manager

    factories = SwitchingFactories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration("steamvr"))
    await settle()
    old_handle = runtime.handle
    await runtime.switch_target(configuration("desktop"))
    await settle()
    assert factories.managers[0].stops == 1
    assert runtime.handle is not old_handle
    assert runtime.active_target == "desktop"
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_overlay_target_routing_apply_settings_stops_before_switching_running_target() -> (
    None
):
    await _assert_target_switch_stops_running_target()


@pytest.mark.asyncio
async def test_overlay_target_routing_apply_settings_stops_after_in_place_target_mutation() -> None:
    await _assert_target_switch_stops_running_target()


@pytest.mark.asyncio
async def test_overlay_start_syncs_bridge_after_preserved_presenter_cleans_refresh_marker() -> None:
    class ChangingPresenter(Presenter):
        def __init__(self) -> None:
            super().__init__()
            self.marker = True

        def snapshot(self) -> object:
            return {"marker": self.marker}

    presenter = ChangingPresenter()

    class CleaningBridge(Bridge):
        def __init__(self) -> None:
            super().__init__()
            self.initial_snapshot: object | None = None
            self.replacements: list[object] = []

        async def start(self) -> None:
            presenter.marker = False

        async def replace_snapshot(self, snapshot: object) -> None:
            self.replacements.append(snapshot)

    class CleaningFactories(Factories):
        def create_bridge(self, **values: object) -> CleaningBridge:
            bridge = CleaningBridge()
            bridge.initial_snapshot = values["presenter_snapshot"]
            self.bridges.append(bridge)
            return bridge

    factories = CleaningFactories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    handle = OverlayRuntimeHandle()
    handle.adopt_presenter(presenter)
    runtime.handle = handle
    await runtime.start_overlay(configuration())
    await settle()
    bridge = factories.bridges[0]
    assert bridge.initial_snapshot == {"marker": True}
    assert bridge.replacements == [{"marker": False}]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_desktop_overlay_start_cleans_preserved_self_refresh_marker_before_initial_snapshot() -> (
    None
):
    presenter = Presenter()
    presenter.self_presentation_refresh_burst = True
    factories = Factories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    handle = OverlayRuntimeHandle()
    handle.adopt_presenter(presenter)
    runtime.handle = handle
    await runtime.start_overlay(configuration("desktop"))
    await settle()
    assert presenter.self_presentation_refresh_burst is False
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_begin_overlay_start_uses_empty_runtime_without_owned_presenter() -> None:
    factories = Factories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    await runtime.start_overlay(configuration())
    await settle()
    assert runtime.handle is not None
    assert runtime.handle.presenter is factories.presenters[0]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_overlay_start_uses_presenter_owned_by_runtime_handle() -> None:
    presenter = Presenter()
    factories = Factories()
    runtime = OverlayOscApplicationRuntime(ingress=Ingress(), factories=factories)
    handle = OverlayRuntimeHandle()
    handle.adopt_presenter(presenter)
    runtime.handle = handle
    await runtime.start_overlay(configuration())
    await settle()
    assert runtime.handle is not None
    assert runtime.handle.presenter is presenter
    assert factories.presenters == []
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stale_overlay_start_after_hub_ingress_closes_runtime_without_legacy_sync() -> None:
    gate = asyncio.Event()
    factories = FreshFactories(gate=gate)
    ingress = Ingress()
    runtime = OverlayOscApplicationRuntime(
        ingress=ingress, factories=factories, renderer_output=Renderer()
    )
    await runtime.start_overlay(configuration())
    stale = runtime.handle
    await runtime.switch_target(configuration("desktop"))
    gate.set()
    await settle()
    assert stale is not runtime.handle
    assert ingress.presenter is runtime.handle.presenter
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stale_overlay_start_exception_after_runtime_replacement_is_ignored() -> None:
    gate = asyncio.Event()
    factories = Factories(gate=gate)
    runtime = OverlayOscApplicationRuntime(
        ingress=Ingress(), factories=factories, renderer_output=Renderer()
    )
    await runtime.start_overlay(configuration())
    stale = runtime.handle
    await runtime.switch_target(configuration("desktop"))
    gate.set()
    await settle()
    assert runtime.handle is not stale
    assert runtime.active_target == "desktop"
    await runtime.shutdown()


class ScenePresenter(Presenter):
    def __init__(self) -> None:
        super().__init__()
        self.scene: dict[str, object] = {"text": "persist me"}
        self.preferences: dict[str, bool] = {}

    def snapshot(self) -> object:
        return {"scene": dict(self.scene), "preferences": dict(self.preferences)}

    async def update_display_preferences(self, **preferences: bool) -> None:
        self.preferences = dict(preferences)


class FreshFactories(Factories):
    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        super().__init__(gate=gate)
        self.managers: list[Manager] = []

    def create_process_manager(self, **values: object) -> Manager:
        manager = Manager()
        self.managers.append(manager)
        return manager


class Renderer:
    async def handle_renderer_event(self, event, *, overlay_instance_id: str) -> None:
        return None


async def _restart_with_scene() -> tuple[OverlayOscApplicationRuntime, ScenePresenter, Factories]:
    presenter = ScenePresenter()
    factories = FreshFactories()
    runtime = OverlayOscApplicationRuntime(
        ingress=Ingress(), factories=factories, renderer_output=Renderer()
    )
    handle = OverlayRuntimeHandle()
    handle.adopt_presenter(presenter)
    runtime.handle = handle
    await runtime.start_overlay(configuration())
    await settle()
    await runtime.switch_target(configuration("desktop"))
    await settle()
    return runtime, presenter, factories


@pytest.mark.asyncio
async def test_overlay_restart_reuses_presenter_scene_for_new_bridge() -> None:
    runtime, presenter, factories = await _restart_with_scene()
    assert runtime.handle.presenter is presenter
    assert factories.bridges[-1].snapshot() == {"ready": True}
    assert presenter.scene == {"text": "persist me"}
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_preserved_overlay_presenter_detaches_from_hub_ingress_until_restart() -> None:
    ingress = Ingress()
    factories = FreshFactories()
    runtime = OverlayOscApplicationRuntime(
        ingress=ingress, factories=factories, renderer_output=Renderer()
    )
    await runtime.start_overlay(configuration())
    await settle()
    presenter = runtime.handle.presenter
    await runtime.switch_target(configuration("desktop"))
    await settle()
    assert runtime.handle.presenter is presenter
    assert ingress.presenter is presenter
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_overlay_restart_detaches_preserved_presenter_from_old_runtime_before_adoption() -> (
    None
):
    runtime, presenter, _factories = await _restart_with_scene()
    assert runtime.handle.presenter is presenter
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_overlay_restart_applies_current_preferences_before_bridge_initial_snapshot() -> None:
    runtime, presenter, _factories = await _restart_with_scene()
    assert presenter.preferences == {"show_translation": True, "show_peer_original": False}
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_run_overlay_start_preserves_traceback_in_detailed_log() -> None:
    class FailingBridge(Bridge):
        async def start(self) -> None:
            raise RuntimeError("boom")

    class FailingFactories(Factories):
        def create_bridge(self, **values: object) -> FailingBridge:
            bridge = FailingBridge()
            self.bridges.append(bridge)
            return bridge

    class SafeLog:
        def __init__(self) -> None:
            self.facts: list[OverlayLogFacts] = []

        def publish_overlay_log_facts(self, facts: OverlayLogFacts) -> None:
            self.facts.append(facts)

    safe_log = SafeLog()
    runtime = OverlayOscApplicationRuntime(
        ingress=Ingress(), factories=FailingFactories(), safe_log=safe_log
    )
    await runtime.start_overlay(configuration())
    await settle()
    assert runtime.lifecycle_state == "failed"
    assert runtime.failure_reason == "unknown"
    assert any(facts.event == "state_changed" for facts in safe_log.facts)
    assert "boom" not in repr(safe_log.facts)


@pytest.mark.asyncio
async def test_apply_settings_updates_vrc_gate_and_reconfigures_receiver() -> None:
    class Effects:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        async def apply_vrc_microphone_intercept(self, enabled: bool) -> bool:
            self.calls.append(enabled)
            return True

    effects = Effects()
    disabled = directive()
    enabled = directive()
    object.__setattr__(enabled, "vrc_mic_intercept", True)
    runtime = OverlayOscApplicationRuntime(vrc_microphone=effects, directive=disabled)
    assert await runtime.apply_overlay_osc(enabled) is True
    assert await runtime.apply_overlay_osc(disabled) is True
    assert effects.calls == [True, False]


def test_schedule_overlay_calibration_emit_preserves_traceback_in_detailed_log() -> None:
    facts = OverlayLogFacts("calibration_update_failed", "steamvr", "overlay-test", "unknown")
    assert facts.failure_reason == "unknown"
    assert "Traceback" not in repr(facts)


@pytest.mark.asyncio
async def test_apply_settings_updates_overlay_presenter_display_preferences() -> None:
    presenter = ScenePresenter()
    handle = OverlayRuntimeHandle()
    handle.attach_presenter(presenter)
    runtime = OverlayOscApplicationRuntime(handle=handle, directive=directive())
    updated = directive()
    object.__setattr__(updated, "show_translation", False)
    object.__setattr__(updated, "show_peer_original", False)
    await runtime.apply_overlay_osc(updated)
    assert presenter.preferences == {"show_translation": False, "show_peer_original": False}


@pytest.mark.asyncio
async def test_apply_settings_pushes_updated_overlay_snapshot_to_bridge_and_restart() -> None:
    runtime, presenter, _factories = await _restart_with_scene()
    presenter.preferences = {"show_translation": False, "show_peer_original": False}
    await runtime.switch_target(configuration())
    await settle()
    assert runtime.handle.presenter is presenter
    assert presenter.preferences["show_translation"] is True
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_apply_settings_pushes_peer_overlay_snapshot_preferences_to_bridge_and_restart() -> (
    None
):
    runtime, presenter, _factories = await _restart_with_scene()
    updated = directive("desktop")
    object.__setattr__(updated, "show_peer_original", False)
    await runtime.apply_overlay_osc(updated)
    await runtime.switch_target(configuration("desktop"))
    await settle()
    assert runtime.handle.presenter is presenter
    assert presenter.preferences["show_peer_original"] is False
    await runtime.shutdown()
