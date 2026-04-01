from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from puripuly_heart.core.overlay.bridge import OverlayBridge
from puripuly_heart.core.overlay.protocol import (
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)


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


class _BlockingInitialSnapshotConnection:
    def __init__(self) -> None:
        self.sent_payloads: list[dict[str, object]] = []
        self.closed = False
        self.initial_send_started = asyncio.Event()
        self.release_initial_send = asyncio.Event()
        self.allow_disconnect = asyncio.Event()

    async def recv(self) -> str:
        return json.dumps({"type": "auth", "session_token": "expected-token"})

    async def send(self, payload: str) -> None:
        message = json.loads(payload)
        if (
            message.get("type") == "snapshot"
            and message.get("payload", {}).get("revision") == 0
            and not self.initial_send_started.is_set()
        ):
            self.initial_send_started.set()
            await self.release_initial_send.wait()
        self.sent_payloads.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        await self.allow_disconnect.wait()
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
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
    )
    await bridge.start()

    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert message["type"] == "snapshot"
    assert message["payload"]["revision"] == 0
    assert message["payload"]["blocks"] == []


@pytest.mark.asyncio
async def test_overlay_bridge_emits_heartbeat_after_authentication() -> None:
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
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
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
    )

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
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
    )
    connection = _AbruptAuthenticatedConnection()

    await bridge._handle_connection(connection)

    assert connection.closed is True
    assert connection.sent_payloads == [
        {
            "type": "snapshot",
            "payload": {
                "revision": 0,
                "calibration": OverlayPresentationCalibration().to_dict(),
                "blocks": [],
            },
        }
    ]
    assert bridge._authenticated_connections == set()


@pytest.mark.asyncio
async def test_overlay_bridge_broadcasts_full_snapshot_replacements() -> None:
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
    )
    await bridge.start()

    try:
        async with connect(bridge.url) as ws:
            await ws.send(json.dumps({"type": "auth", "session_token": "expected-token"}))
            await asyncio.wait_for(ws.recv(), timeout=0.5)

            await bridge.replace_snapshot(
                OverlayPresentationSnapshot(
                    revision=1,
                    calibration=OverlayPresentationCalibration(distance=1.4),
                    blocks=[],
                )
            )

            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
    finally:
        await bridge.stop()

    assert message["type"] == "snapshot"
    assert message["payload"]["revision"] == 1
    assert message["payload"]["calibration"]["distance"] == 1.4


@pytest.mark.asyncio
async def test_overlay_bridge_does_not_send_stale_initial_snapshot_after_newer_live_snapshot() -> (
    None
):
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=0,
            calibration=OverlayPresentationCalibration(),
            blocks=[],
        ),
    )
    connection = _BlockingInitialSnapshotConnection()

    handle_task = asyncio.create_task(bridge._handle_connection(connection))
    await asyncio.wait_for(connection.initial_send_started.wait(), timeout=0.5)

    replace_task = asyncio.create_task(
        bridge.replace_snapshot(
            OverlayPresentationSnapshot(
                revision=1,
                calibration=OverlayPresentationCalibration(distance=1.6),
                blocks=[],
            )
        )
    )
    await asyncio.sleep(0)
    connection.release_initial_send.set()
    await asyncio.wait_for(replace_task, timeout=0.5)
    connection.allow_disconnect.set()
    await handle_task

    assert [payload["payload"]["revision"] for payload in connection.sent_payloads] == [0, 1]


@pytest.mark.asyncio
async def test_overlay_bridge_ignores_stale_snapshot_replacements() -> None:
    bridge = OverlayBridge(
        session_token="expected-token",
        initial_snapshot=OverlayPresentationSnapshot(
            revision=2,
            calibration=OverlayPresentationCalibration(distance=1.4),
            blocks=[],
        ),
    )

    await bridge.replace_snapshot(
        OverlayPresentationSnapshot(
            revision=1,
            calibration=OverlayPresentationCalibration(distance=1.8),
            blocks=[],
        )
    )

    assert bridge.snapshot().revision == 2
    assert bridge.snapshot().calibration.distance == 1.4
