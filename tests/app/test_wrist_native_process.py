import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from puripuly_heart.app.adapters.wrist_vision_process import spawn_wrist_vision
from puripuly_heart.app.services.wrist_vision import WristVisionOwner
from puripuly_heart.config.wrist_vision import WristVisionIntent
from puripuly_heart.core.vision.translation import VisionTranslationService

EXE = Path(__file__).resolve().parents[2] / "build/wrist_vision/PuriPulyHeartWristVision.exe"
pytestmark = pytest.mark.skipif(
    not EXE.is_file(),
    reason="build-wrist-vision.ps1 must build the optional native development component",
)


@pytest.mark.asyncio
async def test_actual_native_mock_handshake_render_and_eof_cleanup():
    process = await spawn_wrist_vision(mock=True, executable=EXE)
    try:
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
        assert ready == dict(type="ready", protocol=1, mock=True)
        for message in [
            dict(
                type="snapshot",
                rows=[
                    dict(id="self:1", role="self", text="Hello\n你好"),
                    dict(id="peer:2", role="peer", text="Good morning\n早上好"),
                ],
            ),
            dict(type="mock_render"),
        ]:
            process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        raw = await asyncio.wait_for(process.stdout.readline(), 10)
        assert raw, f"native mock renderer exited: {await process.wait()}"
        rendered = json.loads(raw)
        assert rendered["type"] == "mock_rendered" and rendered["size"] == [768, 1024]
        assert rendered["checksum"] > 768 * 1024 * 255
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_stopping_one_child_does_not_stop_other_presentation_process():
    first = await spawn_wrist_vision(mock=True, executable=EXE)
    second = await spawn_wrist_vision(mock=True, executable=EXE)
    try:
        assert json.loads(await first.stdout.readline())["type"] == "ready"
        assert json.loads(await second.stdout.readline())["type"] == "ready"
        first.stdin.close()
        await asyncio.wait_for(first.wait(), 5)
        assert second.returncode is None
        second.stdin.write(b'{"type":"mock_render"}\n')
        await second.stdin.drain()
        assert json.loads(await second.stdout.readline())["type"] == "mock_rendered"
    finally:
        for process in (first, second):
            if process.returncode is None:
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 5)


@pytest.mark.asyncio
async def test_real_native_mock_to_python_vision_result_and_owned_shutdown():
    completed = asyncio.Event()
    uploaded = []
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"blocks":[{"id":"1","original":"Test sign","translation":"测试告示"}]}'
                        }
                    }
                ]
            },
        )

    service = VisionTranslationService(
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs
        )
    )

    async def translate(image):
        uploaded.append(image)
        return await service.translate(
            image, key="mock-only-key", model="deepseek-flash", target_language="zh-CN", timeout=5
        )

    preferences = dict(
        config=asdict(WristVisionIntent(enabled=True)),
        translation_enabled=False,
        peer_translation_enabled=False,
    )
    labels = dict(
        title="MOCK Wrist",
        buttons=["Capture", "Older", "Newer", "Close"],
        processing="Processing",
        result="Done",
        error="Error",
        translation_failed="Failed",
        spatial_fallback="Wrist fallback",
    )
    owner = WristVisionOwner(
        spawn=lambda: spawn_wrist_vision(mock=True, executable=EXE),
        preferences=lambda: preferences,
        translate=translate,
        labels=lambda: labels,
        on_state=lambda phase, code: completed.set() if phase == "result" else None,
    )
    try:
        assert await owner.enable()
        process = owner.process
        await owner.send(dict(type="mock_confirm"))
        await asyncio.wait_for(completed.wait(), 5)
        assert len(uploaded) == 1 and uploaded[0].startswith(b"\x89PNG")
        assert len(requests) == 1
        assert owner.phase == "result"
        await owner.disable()
        assert process.returncode == 0 and not owner.running
        assert await owner.enable()
        second = owner.process
        assert second.pid != process.pid
    finally:
        await owner.close()
    assert second.returncode == 0
