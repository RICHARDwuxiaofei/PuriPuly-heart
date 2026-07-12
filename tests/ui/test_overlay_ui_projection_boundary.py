from __future__ import annotations

import pytest

pytest.importorskip("flet")

from puripuly_heart.app.adapters.overlay_ui_projection import DesktopRendererProjection
from puripuly_heart.ui.app import TranslatorApp


class Commands:
    def __init__(self) -> None:
        self.bounds: list[dict[str, int | float]] = []
        self.resets = 0
        self.modes: list[str] = []

    async def persist_desktop_bounds(self, bounds) -> None:  # noqa: ANN001
        self.bounds.append(dict(bounds))

    async def reset_desktop_position(self) -> None:
        self.resets += 1

    def apply_desktop_interaction_mode_event(self, mode: str) -> None:
        self.modes.append(mode)


class Page:
    def __init__(self) -> None:
        self.tasks = []

    def run_task(self, callback) -> None:  # noqa: ANN001
        self.tasks.append(callback)


@pytest.mark.asyncio
async def test_translator_app_routes_typed_desktop_projection_to_application_commands() -> None:
    app = object.__new__(TranslatorApp)
    app.page = Page()
    app.overlay_commands = Commands()
    states = []
    app.on_desktop_overlay_state_changed = lambda **state: states.append(state)

    app._apply_desktop_renderer_projection(
        DesktopRendererProjection(
            "window_bounds_changed",
            "overlay-1",
            (10.0, 20.0, 1152.0, 288.0),
            None,
            "user",
            True,
        )
    )
    app._apply_desktop_renderer_projection(
        DesktopRendererProjection("reset_to_bottom_center_requested", "overlay-1")
    )
    app._apply_desktop_renderer_projection(
        DesktopRendererProjection(
            "interaction_mode_changed", "overlay-1", interaction_mode="pass_through"
        )
    )
    for callback in app.page.tasks:
        await callback()

    assert app.overlay_commands.bounds == [{"x": 10.0, "y": 20.0, "width": 1152.0, "height": 288.0}]
    assert app.overlay_commands.resets == 1
    assert app.overlay_commands.modes == ["pass_through"]
    assert states == [{"interaction_mode": "pass_through", "captions_locked": True}]
