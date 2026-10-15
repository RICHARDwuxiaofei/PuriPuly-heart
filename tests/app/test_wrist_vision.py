import asyncio
import time
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from puripuly_heart.app.services.wrist_vision import WristVisionOwner, captured_image
from puripuly_heart.config.wrist_vision import WristVisionIntent
from puripuly_heart.core.vision.translation import VisionBlock, VisionError
from puripuly_heart.domain.events import UIEvent, UIEventType
from puripuly_heart.domain.models import Transcript, Translation


def capture(identity=1):
    return dict(
        type="capture",
        id=identity,
        size=[2, 2],
        rgb_hex="ffffff" * 4,
        metadata=dict(captured_at_ms=time.time() * 1000, scene_pid=42),
    )


def owner(translate=None):
    preferences = dict(
        config=asdict(WristVisionIntent(enabled=True)),
        translation_enabled=True,
        peer_translation_enabled=True,
    )
    copy = dict(
        title="Test",
        buttons=["Capture", "Older", "Newer", "Close"],
        processing="Processing",
        result="Result",
        error="Failed",
        translation_failed="Failed",
        spatial_fallback="Wrist fallback",
    )
    instance = WristVisionOwner(
        spawn=AsyncMock(),
        preferences=lambda: preferences,
        translate=translate or AsyncMock(return_value=[VisionBlock("1", "Hello", "你好")]),
        labels=lambda: copy,
    )
    instance.running = True
    instance.send = AsyncMock()
    return instance, preferences


@pytest.mark.asyncio
async def test_mock_screenshot_end_to_end_one_request_no_duplicate():
    instance, _ = owner()
    await instance.handle(dict(type="framing"))
    await instance.handle(dict(type="capturing", id=1))
    await instance.handle(capture())
    await instance.handle(capture())
    await instance.job
    instance.translate.assert_awaited_once()
    assert instance.translate.call_args.args[0].startswith(b"\x89PNG")
    result = [
        call.args[0] for call in instance.send.await_args_list if call.args[0]["type"] == "result"
    ]
    assert result[0]["id"] == 1
    assert "你好" in result[0]["rows"][0]["text"]
    assert instance.phase == "result"
    await instance.close()


@pytest.mark.asyncio
async def test_no_upload_without_explicit_native_capture_confirmation():
    instance, _ = owner()
    await instance.handle(capture())
    instance.translate.assert_not_awaited()
    await instance.close()


@pytest.mark.asyncio
async def test_cancel_stale_paid_result_never_published():
    entered = asyncio.Event()

    async def translate(image):
        entered.set()
        await asyncio.Event().wait()

    instance, _ = owner(translate)
    await instance.handle(dict(type="framing"))
    await instance.handle(dict(type="capturing", id=1))
    await instance.handle(capture())
    await asyncio.wait_for(entered.wait(), 1)
    await instance.cancel()
    assert instance.job is None
    assert not any(call.args[0]["type"] == "result" for call in instance.send.await_args_list)
    assert instance.phase == "chat"
    await instance.close()


@pytest.mark.asyncio
async def test_failed_request_logs_safe_summary_without_image_or_content(caplog):
    instance, _ = owner(AsyncMock(side_effect=VisionError("rate_limited", status=429)))
    await instance.handle(dict(type="framing"))
    await instance.handle(dict(type="capturing", id=1))
    message = capture()
    await instance.handle(message)
    await instance.job
    assert instance.code == "rate_limited"
    assert "[WristVision]" in caplog.text and "http_status=429" in caplog.text
    assert "rgb_hex" not in caplog.text and "ffffff" not in caplog.text
    assert "rgb_hex" not in message
    await instance.close()


def test_capture_age_and_format_are_checked():
    message = capture()
    message["metadata"]["captured_at_ms"] -= 60_000
    with pytest.raises(VisionError):
        captured_image(message)
    message = capture()
    message["size"] = [1_000_000, 2]
    with pytest.raises(VisionError):
        captured_image(message)


@pytest.mark.asyncio
async def test_independent_stop_cannot_stop_old_overlay():
    instance, _ = owner()
    old_overlay = SimpleNamespace(close=AsyncMock(), set_enabled=AsyncMock())
    await instance.disable()
    old_overlay.close.assert_not_awaited()
    old_overlay.set_enabled.assert_not_awaited()
    assert instance.phase == "off"


@pytest.mark.asyncio
async def test_native_failure_remains_visible_after_eof(caplog):
    import json

    instance, _ = owner()
    stream = asyncio.StreamReader()
    stream.feed_data((json.dumps(dict(type="error", code="steamvr_unavailable")) + "\n").encode())
    stream.feed_eof()
    process = SimpleNamespace(stdout=stream, returncode=1)
    instance.process = process
    await instance._read(process)
    assert instance.phase == "failed" and instance.code == "steamvr_unavailable"
    assert "steamvr_unavailable" in caplog.text
    await instance.close()


@pytest.mark.asyncio
async def test_rendered_history_modes_preserve_original_and_correlate_late_translation():
    instance, preferences = owner()
    identity = uuid4()
    transcript = Transcript(
        utterance_id=identity, text="Good morning", is_final=True, channel="peer"
    )
    event = UIEvent(
        type=UIEventType.TRANSCRIPT_FINAL, payload=transcript, channel="peer", utterance_id=identity
    )
    instance.publish_event(event)
    instance.publish_event(event)
    translated = Translation(
        utterance_id=identity,
        source_text="Good morning",
        translated_text="早上好",
        source_language="en",
        target_language="zh-CN",
        channel="peer",
    )
    instance.publish_event(
        UIEvent(
            type=UIEventType.TRANSLATION_DONE,
            payload=translated,
            channel="peer",
            utterance_id=identity,
        )
    )
    assert len(instance.history.records) == 1
    await instance.sync()
    rows = [
        call.args[0]["rows"]
        for call in instance.send.await_args_list
        if call.args[0]["type"] == "snapshot"
    ][-1]
    assert "早上好" in rows[0]["text"] and "Good morning" in rows[0]["text"]
    preferences["config"]["peer_display"] = "translation"
    await instance.sync()
    assert [
        call.args[0]["rows"]
        for call in instance.send.await_args_list
        if call.args[0]["type"] == "snapshot"
    ][-1][0]["text"] == "早上好"
    preferences["peer_translation_enabled"] = False
    await instance.sync()
    assert [
        call.args[0]["rows"]
        for call in instance.send.await_args_list
        if call.args[0]["type"] == "snapshot"
    ][-1][0]["text"] == "Good morning"
    await instance.close()
