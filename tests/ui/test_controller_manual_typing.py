from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

ft = pytest.importorskip("flet")

from puripuly_heart.ui import controller as controller_module
from puripuly_heart.ui.controller import GuiController


class FakeOsc:
    def __init__(self) -> None:
        self.reasons: set[str] = set()
        self.calls: list[tuple[str, bool] | tuple[str, None]] = []

    def set_typing_reason(self, reason: str, active: bool) -> None:
        self.calls.append((reason, active))
        if active:
            self.reasons.add(reason)
        else:
            self.reasons.discard(reason)

    def clear_typing_reasons(self) -> None:
        self.calls.append(("clear", None))
        self.reasons.clear()


class FakeHub:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, str]] = []
        self.release = asyncio.Event()

    async def submit_text(self, text: str, *, source: str) -> None:
        self.submissions.append((text, source))
        await self.release.wait()


class FailingHub:
    async def submit_text(self, text: str, *, source: str) -> None:
        _ = (text, source)
        raise RuntimeError("boom")


def _controller() -> GuiController:
    return GuiController(
        page=object(),
        app=object(),
        config_path=Path("settings.json"),
    )


@pytest.mark.asyncio
async def test_manual_input_typing_clears_on_empty_idle_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller()
    controller.osc = FakeOsc()
    monkeypatch.setattr(controller_module, "MANUAL_INPUT_TYPING_IDLE_TIMEOUT_S", 0.01)

    controller.set_manual_input_activity(True)
    assert "manual_input" in controller.osc.reasons
    controller.set_manual_input_activity(False)
    assert "manual_input" not in controller.osc.reasons

    controller.set_manual_input_activity(True)
    await asyncio.sleep(0.02)
    assert "manual_input" not in controller.osc.reasons

    controller.set_manual_input_activity(True)
    await controller.release_manual_typing()

    assert controller._manual_typing_idle_task is None
    assert controller.osc.reasons == set()
    assert ("clear", None) in controller.osc.calls


@pytest.mark.asyncio
async def test_submit_typing_is_generation_safe_and_clears_after_success() -> None:
    controller = _controller()
    controller.osc = FakeOsc()
    hub = FakeHub()
    controller.hub = hub

    controller.set_manual_input_activity(True)
    submit_task = asyncio.create_task(controller.submit_text("hello"))
    await asyncio.sleep(0)

    assert "manual_input" not in controller.osc.reasons
    assert "manual_submit:1" in controller.osc.reasons

    controller._begin_manual_submit_typing()
    assert "manual_submit:2" in controller.osc.reasons
    hub.release.set()
    await submit_task

    assert "manual_submit:1" not in controller.osc.reasons
    assert "manual_submit:2" in controller.osc.reasons
    assert hub.submissions == [("hello", "You")]


@pytest.mark.asyncio
async def test_submit_failure_clears_submit_typing_reason() -> None:
    controller = _controller()
    controller.osc = FakeOsc()
    controller.hub = FailingHub()

    await controller.submit_text("hello")

    assert "manual_submit:1" not in controller.osc.reasons
