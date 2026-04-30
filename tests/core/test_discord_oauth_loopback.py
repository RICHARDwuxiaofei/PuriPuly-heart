from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from puripuly_heart.core import discord_oauth_loopback as loopback
from puripuly_heart.core.discord_oauth_loopback import (
    DISCORD_OAUTH_LOOPBACK_PATH,
    DISCORD_OAUTH_LOOPBACK_PORTS,
    DiscordOAuthCallbackError,
    DiscordOAuthLoopbackClosedError,
    _DiscordOAuthHTTPServer,
    bind_first_available,
)


def _callback_url(listener: object, **params: str) -> str:
    return f"{listener.redirect_uri}?{urllib.parse.urlencode(params)}"


def _get_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_bind_first_available_uses_fixed_loopback_redirect_uri() -> None:
    listener = bind_first_available()
    try:
        assert listener.port in DISCORD_OAUTH_LOOPBACK_PORTS
        assert listener.redirect_uri == (
            f"http://127.0.0.1:{listener.port}{DISCORD_OAUTH_LOOPBACK_PATH}"
        )
    finally:
        listener.close()


def test_success_callback_returns_204_and_wait_returns_code_state() -> None:
    listener = bind_first_available()
    try:
        assert _get_status(_callback_url(listener, code="discord-code-1", state="state-1")) == 204

        result = listener.wait(timeout=2.0)

        assert result.code == "discord-code-1"
        assert result.state == "state-1"
    finally:
        listener.close()


def test_error_callback_returns_204_and_wait_raises_callback_error() -> None:
    listener = bind_first_available()
    try:
        assert _get_status(_callback_url(listener, error="access_denied", state="state-2")) == 204

        with pytest.raises(DiscordOAuthCallbackError) as exc_info:
            listener.wait(timeout=2.0)

        assert exc_info.value.error == "access_denied"
        assert exc_info.value.state == "state-2"
    finally:
        listener.close()


def test_wrong_path_and_missing_parameters_do_not_complete_callback() -> None:
    listener = bind_first_available()
    try:
        wrong_path = f"http://127.0.0.1:{listener.port}/wrong?code=code&state=state"
        assert _get_status(wrong_path) == 404
        assert _get_status(f"{listener.redirect_uri}?code=code-only") == 400
        assert _get_status(f"{listener.redirect_uri}?state=state-only") == 400

        listener.close()
        with pytest.raises(DiscordOAuthLoopbackClosedError):
            listener.wait(timeout=2.0)
    finally:
        listener.close()


def test_close_unblocks_wait_and_stops_listener_thread() -> None:
    listener = bind_first_available()
    started = threading.Event()
    outcome: dict[str, BaseException | object] = {}

    def wait_for_callback() -> None:
        started.set()
        try:
            outcome["result"] = listener.wait(timeout=10.0)
        except BaseException as exc:  # noqa: BLE001 - test captures thread outcome
            outcome["error"] = exc

    thread = threading.Thread(target=wait_for_callback)
    thread.start()
    assert started.wait(timeout=1.0)

    listener.close()
    thread.join(timeout=2.0)

    assert thread.is_alive() is False
    assert isinstance(outcome.get("error"), DiscordOAuthLoopbackClosedError)


def _free_loopback_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def test_server_bind_does_not_enable_reuseaddr_and_uses_windows_exclusive_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exclusive_addr_use = 0x1004
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", exclusive_addr_use, raising=False)
    calls: list[tuple[str, int, int, int] | tuple[str, tuple[str, int]]] = []

    class FakeSocket:
        def setsockopt(self, level: int, option: int, value: int) -> None:
            calls.append(("setsockopt", level, option, value))

        def bind(self, address: tuple[str, int]) -> None:
            calls.append(("bind", address))

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", DISCORD_OAUTH_LOOPBACK_PORTS[0])

    server = _DiscordOAuthHTTPServer.__new__(_DiscordOAuthHTTPServer)
    server.socket = FakeSocket()
    server.server_address = ("127.0.0.1", DISCORD_OAUTH_LOOPBACK_PORTS[0])

    server.server_bind()

    assert _DiscordOAuthHTTPServer.allow_reuse_address is False
    assert calls == [
        ("setsockopt", socket.SOL_SOCKET, exclusive_addr_use, 1),
        ("bind", ("127.0.0.1", DISCORD_OAUTH_LOOPBACK_PORTS[0])),
    ]


def test_occupied_configured_port_cannot_be_reused_and_falls_back_to_next_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    occupied_port = int(occupied.getsockname()[1])
    fallback_port = _free_loopback_port()
    monkeypatch.setattr(
        loopback,
        "DISCORD_OAUTH_LOOPBACK_PORTS",
        (occupied_port, fallback_port),
    )

    try:
        listener = bind_first_available()
        try:
            assert listener.port == fallback_port
        finally:
            listener.close()
    finally:
        if occupied is not None:
            occupied.close()
