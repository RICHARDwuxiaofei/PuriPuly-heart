from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NoReturn
from urllib.parse import parse_qs, urlsplit

DISCORD_OAUTH_LOOPBACK_PORTS = (62187, 62188, 62189)
DISCORD_OAUTH_LOOPBACK_HOST = "127.0.0.1"
DISCORD_OAUTH_LOOPBACK_PATH = "/discord/callback"


@dataclass(frozen=True, slots=True)
class DiscordOAuthCallbackResult:
    code: str
    state: str


class DiscordOAuthCallbackError(Exception):
    def __init__(self, error: str, state: str) -> None:
        super().__init__(error)
        self.error = error
        self.state = state


class DiscordOAuthLoopbackClosedError(RuntimeError):
    pass


class _DiscordOAuthHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@dataclass(slots=True)
class DiscordOAuthLoopbackListener:
    port: int
    _server: _DiscordOAuthHTTPServer
    _thread: threading.Thread
    _event: threading.Event
    _lock: threading.Lock
    _result: DiscordOAuthCallbackResult | None = None
    _error: DiscordOAuthCallbackError | None = None
    _closed: bool = False

    @classmethod
    def bind(cls, port: int) -> DiscordOAuthLoopbackListener:
        listener = cls.__new__(cls)
        listener.port = port
        listener._event = threading.Event()
        listener._lock = threading.Lock()
        listener._result = None
        listener._error = None
        listener._closed = False
        listener._server = _DiscordOAuthHTTPServer(
            (DISCORD_OAUTH_LOOPBACK_HOST, port),
            _handler_for(listener),
        )
        listener._thread = threading.Thread(
            target=listener._server.serve_forever,
            name=f"discord-oauth-loopback-{port}",
            daemon=True,
        )
        listener._thread.start()
        return listener

    @property
    def redirect_uri(self) -> str:
        return f"http://{DISCORD_OAUTH_LOOPBACK_HOST}:{self.port}{DISCORD_OAUTH_LOOPBACK_PATH}"

    def wait(self, timeout: float | None = None) -> DiscordOAuthCallbackResult:
        if not self._event.wait(timeout):
            raise TimeoutError("timed out waiting for Discord OAuth callback")
        if self._result is not None:
            return self._result
        if self._error is not None:
            raise self._error
        raise DiscordOAuthLoopbackClosedError("Discord OAuth loopback listener was closed")

    def close(self) -> None:
        should_stop = False
        with self._lock:
            if not self._closed:
                self._closed = True
                should_stop = True
                self._event.set()

        if should_stop:
            self._server.shutdown()
            self._server.server_close()

        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def cancel(self) -> None:
        self.close()

    def _complete(
        self,
        *,
        result: DiscordOAuthCallbackResult | None = None,
        error: DiscordOAuthCallbackError | None = None,
    ) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._result = result
            self._error = error
            self._event.set()

    def _close_async(self) -> None:
        closer = threading.Thread(
            target=self.close,
            name=f"discord-oauth-loopback-{self.port}-closer",
            daemon=True,
        )
        closer.start()


def bind_first_available() -> DiscordOAuthLoopbackListener:
    last_error: OSError | None = None
    for port in DISCORD_OAUTH_LOOPBACK_PORTS:
        try:
            return DiscordOAuthLoopbackListener.bind(port)
        except OSError as exc:
            last_error = exc
    raise OSError("no Discord OAuth loopback port is available") from last_error


def _handler_for(listener: DiscordOAuthLoopbackListener) -> type[BaseHTTPRequestHandler]:
    class DiscordOAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != DISCORD_OAUTH_LOOPBACK_PATH:
                self.send_error(404)
                return

            params = parse_qs(parsed.query, keep_blank_values=True)
            state = _single_non_empty(params, "state")
            code = _single_non_empty(params, "code")
            oauth_error = _single_non_empty(params, "error")

            if state is not None and oauth_error is not None:
                self.send_response(204)
                self.end_headers()
                listener._complete(error=DiscordOAuthCallbackError(oauth_error, state))
                listener._close_async()
                return

            if state is not None and code is not None:
                self.send_response(204)
                self.end_headers()
                listener._complete(result=DiscordOAuthCallbackResult(code=code, state=state))
                listener._close_async()
                return

            self.send_error(400)

        def log_message(self, format: str, *args: object) -> None:
            _ = format, args

    return DiscordOAuthCallbackHandler


def _single_non_empty(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values or len(values) != 1:
        return None
    value = values[0]
    return value or None


def _unreachable() -> NoReturn:
    raise AssertionError("unreachable")
