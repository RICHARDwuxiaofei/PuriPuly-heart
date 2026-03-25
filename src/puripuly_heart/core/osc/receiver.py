from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

if TYPE_CHECKING:
    from puripuly_heart.core.orchestrator.hub import ClientHub

logger = logging.getLogger(__name__)


class VrcOscReceiver:
    def __init__(self, hub: ClientHub, host: str = "127.0.0.1", port: int = 9001):
        self.hub = hub
        self.host = host
        self.port = port
        self.transport = None
        self._mute_task: asyncio.Task | None = None
        self.mute_delay = 0.4  # 延迟 0.4 秒闭麦，完美包容 PTT 习惯，可自行微调

    def mute_handler(self, address: str, *args) -> None:
        if not args:
            return
        is_muted = bool(args[0])

        # 每次收到 OSC 信号，先取消之前的延时任务（防止快速开关麦导致状态错乱）
        if self._mute_task and not self._mute_task.done():
            self._mute_task.cancel()

        loop = asyncio.get_running_loop()
        self._mute_task = loop.create_task(self._apply_mute_state(is_muted))

    async def _apply_mute_state(self, is_muted: bool) -> None:
        try:
            if is_muted:
                # 核心逻辑：如果是闭麦，等待 0.4 秒，让尾音飞一会儿
                await asyncio.sleep(self.mute_delay)

            # 如果是开麦，或者延时结束了，才真正修改 Hub 的状态
            if self.hub.vrc_muted != is_muted:
                logger.info(f"[OSC Receiver] VRChat Mic Muted State Applied: {is_muted}")
                self.hub.vrc_muted = is_muted

        except asyncio.CancelledError:
            # 如果在等待的 0.4 秒内，用户又按下了开麦键，任务会被取消，什么都不做
            pass

    async def start(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.map("/avatar/parameters/MuteSelf", self.mute_handler)

        loop = asyncio.get_running_loop()
        try:
            server = AsyncIOOSCUDPServer((self.host, self.port), dispatcher, loop)
            self.transport, _ = await server.create_serve_endpoint()
        except OSError:
            logger.exception(
                "[OSC Receiver] Failed to start AsyncIOOSCUDPServer on %s:%s",
                self.host,
                self.port,
            )
            raise
        logger.info(f"[OSC Receiver] Listening on {self.host}:{self.port} for VRChat parameters")

    def stop(self) -> None:
        if self._mute_task and not self._mute_task.done():
            self._mute_task.cancel()
        if self.transport:
            self.transport.close()
            logger.info("[OSC Receiver] Stopped listening.")
