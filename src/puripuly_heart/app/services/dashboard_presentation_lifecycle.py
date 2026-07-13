from __future__ import annotations

import asyncio

from puripuly_heart.app.ports.dashboard_presentation import (
    DashboardPresentationContextPort,
    DashboardPresentationViewPort,
    PresentationEventBridgeFactoryPort,
    PresentationEventBridgeRequest,
    PresentationEventRuntimePort,
    PresentationRuntimeLoggingPort,
)
from puripuly_heart.domain.events import UIEvent


class DashboardPresentationLifecycle:
    def __init__(
        self,
        *,
        view: DashboardPresentationViewPort,
        bridge_factory: PresentationEventBridgeFactoryPort,
        event_queue,
        context: DashboardPresentationContextPort,
        output_runtime: PresentationEventRuntimePort,
        runtime_logging: PresentationRuntimeLoggingPort | None,
    ) -> None:
        self._view = view
        self._bridge_factory = bridge_factory
        self._event_queue: asyncio.Queue[UIEvent] = event_queue
        self._context = context
        self._output_runtime = output_runtime
        self._runtime_logging = runtime_logging
        self._prepared = False
        self._started = False
        self._frozen = False
        self._unsubscribe_managed = None

    async def prepare_presentation(self) -> None:
        if self._prepared:
            return
        await self._view.prepare_dashboard()
        self._prepared = True

    async def start_rendering(self) -> None:
        if self._started:
            return
        bridge = self._bridge_factory.create_event_bridge(
            PresentationEventBridgeRequest(
                event_queue=self._event_queue,
                context=self._context,
                runtime_logging=self._runtime_logging,
            )
        )
        self._output_runtime.start_ui_event_bridge(bridge)
        self._unsubscribe_managed = self._context.subscribe_managed_authentication_presentation(
            self._view.on_managed_authentication_presentation
        )
        await self._view.start_dashboard()
        self._started = True

    async def freeze_application_ingress(self) -> None:
        if self._frozen:
            return
        await self._view.freeze_dashboard_ingress()
        self._frozen = True

    async def stop_rendering(self, failures: tuple[BaseException, ...]) -> None:
        if self._unsubscribe_managed is not None:
            self._unsubscribe_managed()
            self._unsubscribe_managed = None
        await self._output_runtime.stop_ui_event_bridge()
        await self._view.stop_dashboard(failures)
        self._started = False


__all__ = ["DashboardPresentationLifecycle"]
