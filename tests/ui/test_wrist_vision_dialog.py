from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from puripuly_heart.app.services.wrist_vision import WristVisionOwner
from puripuly_heart.config.wrist_vision import WristVisionIntent
from puripuly_heart.ui.components.wrist_vision_dialog import WristVisionDialog, wrist_labels
from puripuly_heart.ui.i18n import get_locale, set_locale
from tests.helpers.flet_page import DialogTrackingPage


@pytest.mark.asyncio
async def test_chinese_missing_component_is_visible_and_logged_after_dialog_close(caplog):
    previous = get_locale()
    set_locale("zh-CN")
    page = DialogTrackingPage()
    page.update = lambda: None
    values = asdict(WristVisionIntent())
    application = SimpleNamespace(
        wrist_vision_preferences=lambda: dict(config=dict(values)),
        configure_wrist_vision=AsyncMock(),
    )
    owner = WristVisionOwner(
        spawn=AsyncMock(side_effect=FileNotFoundError()),
        preferences=application.wrist_vision_preferences,
        translate=AsyncMock(),
        labels=wrist_labels,
    )
    dialog = WristVisionDialog(page, application, owner, lambda f: None)
    try:
        dialog.open()
        dialog.values["enabled"] = True
        await dialog.apply()
        assert "失败" in dialog.status.value
        assert "wrist_component_missing" in dialog.status.value
        page.pop_dialog()
        assert "[WristVision]" in caplog.text and "code=wrist_component_missing" in caplog.text
        assert not owner.running
    finally:
        await owner.close()
        set_locale(previous)


@pytest.mark.asyncio
async def test_turning_wrist_off_does_not_submit_image():
    page = DialogTrackingPage()
    page.update = lambda: None
    application = SimpleNamespace(
        wrist_vision_preferences=lambda: dict(config=asdict(WristVisionIntent())),
        configure_wrist_vision=AsyncMock(),
    )
    owner = SimpleNamespace(
        phase="off", disable=AsyncMock(), cancel=AsyncMock(), enable=AsyncMock()
    )
    dialog = WristVisionDialog(page, application, owner, lambda f: None)
    dialog.open()
    await dialog.apply()
    owner.disable.assert_awaited_once()
    owner.enable.assert_not_awaited()
