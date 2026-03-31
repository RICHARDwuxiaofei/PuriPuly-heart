from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from puripuly_heart.core.overlay.bridge import OverlayBridge


class _AbruptAuthenticatedConnection:
    def __init__(self) -> None:
        self.sent_payloads: list[dict[str, object]] = []
        self.closed = False

    async def recv(self) -> str:
        return json.dumps({"type": "auth", "session_token": "expected-token"})

    async def send(self, payload: str) -> None:
        self.sent_payloads.append(json.loads(payload))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        raise ConnectionClosedError(None, None)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_overlay_bridge_requires_session_token() -> None:
    bridge = OverlayBridge(session_token="expected-token")
    await bridge.start()

    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "wrong-token"}))
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert message["type"] == "auth_error"


@pytest.mark.asyncio
async def test_overlay_bridge_sends_authenticated_initial_snapshot() -> None:
    bridge = OverlayBridge(session_token="expected-token", initial_snapshot={"events": []})
    await bridge.start()

    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert message["type"] == "snapshot"
    assert message["payload"]["events"] == []


@pytest.mark.asyncio
async def test_overlay_bridge_emits_heartbeat_after_authentication() -> None:
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot={"events": []},
        heartbeat_interval_ms=50,
    )
    await bridge.start()

    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            await asyncio.wait_for(ws.recv(), timeout=0.5)
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert message["type"] == "heartbeat"


@pytest.mark.asyncio
async def test_overlay_bridge_resets_one_time_token_after_stop_and_restart() -> None:
    bridge = OverlayBridge(session_token="expected-token", initial_snapshot={"events": []})

    await bridge.start()
    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            first_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
            await ws.send(json.dumps({"type": "runtime_error", "failure_reason": "boom"}))
            queued = await asyncio.wait_for(bridge.messages.get(), timeout=0.5)
            assert queued["type"] == "runtime_error"
    finally:
        await bridge.stop()

    assert bridge.messages.empty()

    await bridge.start()
    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            second_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert first_message["type"] == "snapshot"
    assert second_message["type"] == "snapshot"


@pytest.mark.asyncio
async def test_overlay_bridge_swallows_authenticated_disconnect_without_close_frame() -> None:
    bridge = OverlayBridge(session_token="expected-token", initial_snapshot={"events": []})
    connection = _AbruptAuthenticatedConnection()

    await bridge._handle_connection(connection)

    assert connection.closed is True
    assert connection.sent_payloads == [{"type": "snapshot", "payload": {"events": []}}]
    assert bridge._authenticated_connections == set()
