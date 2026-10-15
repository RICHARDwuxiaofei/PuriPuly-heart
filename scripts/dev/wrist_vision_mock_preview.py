from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image

from puripuly_heart.app.adapters.wrist_vision_process import spawn_wrist_vision


async def main():
    root = Path(__file__).resolve().parents[2]
    process = await spawn_wrist_vision(
        mock=True, executable=root / "build/wrist_vision/PuriPulyHeartWristVision.exe"
    )
    try:
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
        assert ready["mock"]
        messages = [
            dict(
                type="config",
                title="MOCK · PuriPuly Wrist Vision",
                values={},
                labels=["截图翻译", "较早消息", "较新消息", "返回 / 关闭"],
            ),
            dict(type="status", text="MOCK ONLY · No headset capture or paid API call"),
            dict(
                type="snapshot",
                rows=[
                    dict(id="self:1", role="self", text="早上好，朋友。\n→ Good morning, friend."),
                    dict(
                        id="peer:2",
                        role="peer",
                        text="Welcome to this world!\n→ 欢迎来到这个世界！",
                    ),
                    dict(
                        id="self:3",
                        role="self",
                        text="这个告示牌上写了什么？\n→ What does this sign say?",
                    ),
                ],
            ),
            dict(type="mock_render", dump_rgba=True),
        ]
        for message in messages:
            process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
        assert response["type"] == "mock_rendered"
        output = root / ".cache/wrist-vision-mock.png"
        Image.frombytes("RGBA", tuple(response["size"]), bytes.fromhex(response["rgba_hex"])).save(
            output
        )
        print("Native GDI mock panel (not a VR hardware screenshot):", output)
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)


if __name__ == "__main__":
    asyncio.run(main())
