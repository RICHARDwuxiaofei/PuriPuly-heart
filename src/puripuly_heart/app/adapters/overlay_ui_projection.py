from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from puripuly_heart.app.ports.post_commit_runtime import DashboardRetryFactsDirective


@dataclass(frozen=True, slots=True)
class DesktopRendererProjection:
    event: str
    overlay_instance_id: str
    bounds: tuple[float, float, float, float] | None = None
    interaction_mode: str | None = None
    source: str | None = None
    persist: bool = False


@dataclass(slots=True)
class ProductionUiProjection:
    dashboard_facts: DashboardRetryFactsDirective | None = None
    desktop: DesktopRendererProjection | None = None
    _dashboard_listeners: list[Callable[[DashboardRetryFactsDirective], None]] = field(
        default_factory=list
    )
    _desktop_listeners: list[Callable[[DesktopRendererProjection], None]] = field(
        default_factory=list
    )

    def publish_dashboard_runtime_facts(self, facts: DashboardRetryFactsDirective) -> None:
        self.dashboard_facts = facts
        for listener in tuple(self._dashboard_listeners):
            listener(facts)

    async def handle_renderer_event(
        self, event: Mapping[str, object], *, overlay_instance_id: str
    ) -> None:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return
        event_name = payload.get("event")
        if event_name not in {
            "window_bounds_changed",
            "reset_to_bottom_center_requested",
            "interaction_mode_changed",
        }:
            return
        bounds = None
        if event_name == "window_bounds_changed":
            values = tuple(payload.get(key) for key in ("x", "y", "width", "height"))
            if not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) for value in values
            ):
                return
            bounds = tuple(float(value) for value in values)
        mode = payload.get("mode")
        source = payload.get("source")
        persist = payload.get("persist")
        projection = DesktopRendererProjection(
            event_name,
            overlay_instance_id,
            bounds,
            mode if isinstance(mode, str) else None,
            source if isinstance(source, str) else None,
            persist if isinstance(persist, bool) else False,
        )
        self.desktop = projection
        for listener in tuple(self._desktop_listeners):
            listener(projection)

    def subscribe_dashboard(
        self, listener: Callable[[DashboardRetryFactsDirective], None]
    ) -> Callable[[], None]:
        self._dashboard_listeners.append(listener)
        if self.dashboard_facts is not None:
            listener(self.dashboard_facts)
        return lambda: self._remove(self._dashboard_listeners, listener)

    def subscribe_desktop(
        self, listener: Callable[[DesktopRendererProjection], None]
    ) -> Callable[[], None]:
        self._desktop_listeners.append(listener)
        if self.desktop is not None:
            listener(self.desktop)
        return lambda: self._remove(self._desktop_listeners, listener)

    @staticmethod
    def _remove(listeners: list, listener: object) -> None:  # noqa: ANN401
        if listener in listeners:
            listeners.remove(listener)
