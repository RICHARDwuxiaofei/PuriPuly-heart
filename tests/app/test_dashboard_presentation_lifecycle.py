from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

import puripuly_heart.app.ports.dashboard_presentation as port
import puripuly_heart.app.services.dashboard_presentation_lifecycle as lifecycle
from puripuly_heart.app.ports.dashboard_presentation import DashboardPresentationEventContext
from puripuly_heart.app.services.dashboard_presentation_lifecycle import (
    DashboardPresentationLifecycle,
)
from puripuly_heart.app.services.managed_authentication_application import (
    managed_authentication_presentation,
)
from puripuly_heart.ui.app import TranslatorApp
from puripuly_heart.ui.event_bridge import ApplicationUIEventBridgeFactory


@pytest.mark.asyncio
async def test_presentation_lifecycle_binds_output_owned_bridge_and_awaits_shutdown() -> None:
    events: list[str] = []

    class View:
        async def prepare_dashboard(self) -> None:
            events.append("prepare")

        async def start_dashboard(self) -> None:
            events.append("start")

        async def freeze_dashboard_ingress(self) -> None:
            events.append("freeze")

        async def stop_dashboard(self, failures: tuple[BaseException, ...]) -> None:
            events.append(f"stop:{len(failures)}")

        def on_managed_authentication_presentation(self, presentation) -> None:  # noqa: ANN001
            return None

    class Bridge:
        async def run(self) -> None:
            await asyncio.Event().wait()

        async def close(self) -> None:
            events.append("bridge_close")

    class Factory:
        def create_event_bridge(self, request):  # noqa: ANN001, ANN201
            assert request.context.current_event_context().source_language == "ja"
            return Bridge()

    class Context:
        def current_event_context(self) -> DashboardPresentationEventContext:
            return DashboardPresentationEventContext("ja", "en", True, "idle", "basic")

        def clear_managed_auth_pending(self) -> None:
            return None

        def observe_translation_success(self) -> None:
            return None

        async def record_translation_success(self) -> None:
            return None

        def subscribe_managed_authentication_presentation(self, listener):  # noqa: ANN001, ANN201
            assert callable(listener)
            return lambda: events.append("managed_unsubscribe")

    class Output:
        def start_ui_event_bridge(self, bridge):  # noqa: ANN001, ANN201
            events.append("bind")
            self.bridge = bridge
            self.task = asyncio.create_task(bridge.run())
            return self.task

        async def stop_ui_event_bridge(self) -> None:
            events.append("unbind")
            self.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await self.task
            await self.bridge.close()

    lifecycle = DashboardPresentationLifecycle(
        view=View(),
        bridge_factory=Factory(),
        event_queue=asyncio.Queue(),
        context=Context(),
        output_runtime=Output(),
        runtime_logging=None,
    )
    await lifecycle.prepare_presentation()
    await lifecycle.start_rendering()
    await lifecycle.freeze_application_ingress()
    await lifecycle.stop_rendering(())

    assert events == [
        "prepare",
        "bind",
        "start",
        "freeze",
        "managed_unsubscribe",
        "unbind",
        "bridge_close",
        "stop:0",
    ]


def test_application_presentation_boundary_does_not_import_ui() -> None:
    assert "puripuly_heart.ui" not in inspect.getsource(port)
    assert "puripuly_heart.ui" not in inspect.getsource(lifecycle)


@pytest.mark.asyncio
async def test_real_translator_app_adapter_starts_projects_history_and_awaits_shutdown() -> None:
    events: list[str] = []
    app = TranslatorApp.__new__(TranslatorApp)
    app.view_dashboard = SimpleNamespace(history_items=[])
    app.view_logs = SimpleNamespace(append_conversation_record=lambda **_kwargs: None)
    app.show_snackbar = lambda *_args: None
    app.on_telemetry_translation_success = lambda: None
    app.on_overlay_state_changed = lambda **_kwargs: None

    async def prepare() -> None:
        events.append("prepare")

    async def start() -> None:
        events.append("start")

    async def freeze() -> None:
        events.append("freeze")

    async def stop(_failures) -> None:  # noqa: ANN001
        events.append("stop")

    app.prepare_dashboard = prepare
    app.start_dashboard = start
    app.freeze_dashboard_ingress = freeze
    app.stop_dashboard = stop

    class Context:
        def current_event_context(self) -> DashboardPresentationEventContext:
            return DashboardPresentationEventContext("ja", "en", True, "idle", "basic")

        def clear_managed_auth_pending(self) -> None:
            return None

        def observe_translation_success(self) -> None:
            return None

        def subscribe_managed_authentication_presentation(self, listener):  # noqa: ANN001, ANN201
            assert callable(listener)
            return lambda: events.append("managed_unsubscribe")

    class Output:
        def start_ui_event_bridge(self, bridge):  # noqa: ANN001, ANN201
            self.bridge = bridge
            events.append("bind")

        async def stop_ui_event_bridge(self) -> None:
            events.append("unbind")
            self.bridge.close()
            events.append("bridge_close")

    output = Output()
    owner = DashboardPresentationLifecycle(
        view=app,
        bridge_factory=ApplicationUIEventBridgeFactory(app),
        event_queue=asyncio.Queue(),
        context=Context(),
        output_runtime=output,
        runtime_logging=None,
    )

    await owner.prepare_presentation()
    await owner.start_rendering()
    output.bridge.history_destination.append_entry(
        "Mic", "hello", translated=True, language_code="en"
    )
    await owner.freeze_application_ingress()
    await owner.stop_rendering(())

    assert app.view_dashboard.history_items == [("Mic", "hello", True, "en")]
    assert events == [
        "prepare",
        "bind",
        "start",
        "freeze",
        "managed_unsubscribe",
        "unbind",
        "bridge_close",
        "stop",
    ]


def test_translator_app_projects_live_managed_callback_and_reopen_state() -> None:
    projected: list[object] = []
    dialog = SimpleNamespace(
        set_reopen_available=lambda available, action: projected.append((available, action)),
        set_callback_received=lambda: projected.append("callback"),
    )
    app = TranslatorApp.__new__(TranslatorApp)
    app._discord_managed_auth_dialog = dialog
    presentation = managed_authentication_presentation(
        action="in_progress",
        prompt="discord",
        connection_state="disconnected",
        browser_reopen_available=True,
        referral_bonus_applied=False,
        callback_received=True,
    )

    app.on_managed_authentication_presentation(presentation)

    assert app._managed_authentication_presentation is presentation
    assert projected[0][0] is True
    assert callable(projected[0][1])
    assert projected[1] == "callback"
