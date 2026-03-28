from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from .protocol import OverlayEventUnion, OverlayStateSnapshot


@dataclass(slots=True)
class OverlayBridge:
    session_token: str
    initial_snapshot: dict[str, object] | OverlayStateSnapshot | None = None
    heartbeat_interval_ms: int = 1000
    host: str = "127.0.0.1"
    port: int = 0

    url: str = field(init=False, default="")
    messages: asyncio.Queue[dict[str, Any]] = field(
        init=False,
        default_factory=asyncio.Queue,
    )
    _server: Server | None = field(init=False, default=None)
    _heartbeat_task: asyncio.Task[None] | None = field(init=False, default=None)
    _authenticated_connections: set[ServerConnection] = field(
        init=False,
        default_factory=set,
    )
    _snapshot: OverlayStateSnapshot = field(init=False)
    _token_consumed: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if self.initial_snapshot is None:
            self._snapshot = OverlayStateSnapshot(events=[])
            return
        if isinstance(self.initial_snapshot, OverlayStateSnapshot):
            self._snapshot = self.initial_snapshot
            return
        self._snapshot = OverlayStateSnapshot.from_dict(self.initial_snapshot)

    async def start(self) -> None:
        if self._server is not None:
            return

        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=None,
        )
        socket = self._server.sockets[0]
        bound_host, bound_port = socket.getsockname()[:2]
        self.url = f"ws://{bound_host}:{bound_port}"
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat_loop())

    async def stop(self) -> None:
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        connections = list(self._authenticated_connections)
        self._authenticated_connections.clear()
        for connection in connections:
            await connection.close()

        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        self.url = ""

    async def emit(self, event: OverlayEventUnion) -> None:
        self._snapshot.events.append(event)
        await self._broadcast_json(event.to_dict())

    def snapshot(self) -> OverlayStateSnapshot:
        return OverlayStateSnapshot(events=list(self._snapshot.events))

    async def _handle_connection(self, connection: ServerConnection) -> None:
        authenticated = False
        try:
            auth_payload = self._load_message(await connection.recv())
            if not self._is_valid_auth_payload(auth_payload):
                await connection.send(json.dumps({"type": "auth_error"}))
                return

            authenticated = True
            self._token_consumed = True
            self._authenticated_connections.add(connection)
            await connection.send(
                json.dumps(
                    {
                        "type": "snapshot",
                        "payload": self.snapshot().to_dict(),
                    }
                )
            )

            async for raw_message in connection:
                message = self._load_message(raw_message)
                await self.messages.put(message)
        finally:
            if authenticated:
                self._authenticated_connections.discard(connection)
            await connection.close()

    def _is_valid_auth_payload(self, payload: dict[str, Any]) -> bool:
        return (
            payload.get("type") == "auth"
            and payload.get("session_token") == self.session_token
            and not self._token_consumed
        )

    def _load_message(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, str):
            raise ValueError("overlay bridge payload must be text JSON")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("overlay bridge payload must decode to an object")
        return data

    async def _run_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval_ms / 1000.0)
                await self._broadcast_json({"type": "heartbeat"})
        except asyncio.CancelledError:
            raise

    async def _broadcast_json(self, payload: dict[str, Any]) -> None:
        if not self._authenticated_connections:
            return

        message = json.dumps(payload)
        stale_connections: list[ServerConnection] = []
        for connection in tuple(self._authenticated_connections):
            try:
                await connection.send(message)
            except Exception:
                stale_connections.append(connection)

        for connection in stale_connections:
            self._authenticated_connections.discard(connection)
