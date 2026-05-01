from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NoReturn
from urllib.parse import parse_qs, urlsplit

DISCORD_OAUTH_LOOPBACK_PORTS = (62187, 62188, 62189)
DISCORD_OAUTH_LOOPBACK_HOST = "127.0.0.1"
DISCORD_OAUTH_LOOPBACK_PATH = "/discord/callback"
DISCORD_OAUTH_CALLBACK_TITLE_KEY = "discord_auth.callback.title"
DISCORD_OAUTH_CALLBACK_COMPLETION_LINE_KEYS = (
    "discord_auth.callback.line1",
    "discord_auth.callback.line2",
    "discord_auth.callback.line3",
)
DISCORD_OAUTH_CALLBACK_COMPLETION_FALLBACK_LINES = (
    "We received your Discord verification",
    "PuriPuly is finishing your Managed key setup.",
    "You can close this tab.",
)
DISCORD_OAUTH_CALLBACK_FONT_FAMILIES = {
    "en": "system-ui, sans-serif",
    "ko": '"NanumSquareRound", system-ui, sans-serif',
    "ja": '"M PLUS Rounded 1c", system-ui, sans-serif',
    "zh-CN": '"ResourceHanRoundedCN", system-ui, sans-serif',
}


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
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        exclusive_addr_use = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive_addr_use is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive_addr_use, 1)
        super().server_bind()


@dataclass(slots=True)
class DiscordOAuthLoopbackListener:
    port: int
    locale: str
    _server: _DiscordOAuthHTTPServer
    _thread: threading.Thread
    _event: threading.Event
    _lock: threading.Lock
    _result: DiscordOAuthCallbackResult | None = None
    _error: DiscordOAuthCallbackError | None = None
    _closed: bool = False

    @classmethod
    def bind(cls, port: int, *, locale: str | None = None) -> DiscordOAuthLoopbackListener:
        listener = cls.__new__(cls)
        listener.port = port
        listener.locale = _resolve_callback_locale(locale)
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


def bind_first_available(*, locale: str | None = None) -> DiscordOAuthLoopbackListener:
    last_error: OSError | None = None
    for port in DISCORD_OAUTH_LOOPBACK_PORTS:
        try:
            return DiscordOAuthLoopbackListener.bind(port, locale=locale)
        except OSError as exc:
            last_error = exc
    raise OSError("no Discord OAuth loopback port is available") from last_error


def _resolve_callback_locale(locale: str | None) -> str:
    try:
        from puripuly_heart.ui.i18n import get_locale, resolve_locale

        return resolve_locale(locale if locale is not None else get_locale())
    except Exception:
        return "en"


def _callback_completion_line(locale: str, key: str, fallback: str) -> str:
    try:
        from puripuly_heart.ui.i18n import t_for_locale

        return t_for_locale(locale, key, default=fallback)
    except Exception:
        return fallback


def _callback_completion_page(locale: str) -> bytes:
    resolved_locale = _resolve_callback_locale(locale)
    title = _callback_completion_line(
        resolved_locale,
        DISCORD_OAUTH_CALLBACK_TITLE_KEY,
        "PuriPuly",
    )
    lines = [
        _callback_completion_line(resolved_locale, key, fallback)
        for key, fallback in zip(
            DISCORD_OAUTH_CALLBACK_COMPLETION_LINE_KEYS,
            DISCORD_OAUTH_CALLBACK_COMPLETION_FALLBACK_LINES,
            strict=True,
        )
    ]
    lines_html = "<br>\n".join(escape(line) for line in lines)
    font_family = DISCORD_OAUTH_CALLBACK_FONT_FAMILIES.get(
        resolved_locale,
        DISCORD_OAUTH_CALLBACK_FONT_FAMILIES["en"],
    )
    html = f"""<!doctype html>
<html lang="{escape(resolved_locale, quote=True)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 32px;
      background: #FFF8F6;
      color: #5C4D4C;
      font-family: {font_family};
    }}
    main {{
      max-width: 46rem;
      text-align: center;
      font-size: clamp(24px, 4vw, 32px);
      line-height: 1.6;
      font-weight: 600;
      word-break: keep-all;
      overflow-wrap: break-word;
    }}
    p {{ margin: 0; }}
  </style>
</head>
<body>
  <main>
    <p>{lines_html}</p>
  </main>
</body>
</html>
"""
    return html.encode("utf-8")


def render_discord_oauth_callback_completion_page(locale: str | None = None) -> bytes:
    return _callback_completion_page(_resolve_callback_locale(locale))


def _send_success_callback_response(
    handler: BaseHTTPRequestHandler,
    listener: DiscordOAuthLoopbackListener,
    result: DiscordOAuthCallbackResult,
) -> None:
    listener._complete(result=result)
    try:
        body = _callback_completion_page(listener.locale)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    finally:
        listener._close_async()


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
                _send_success_callback_response(
                    self,
                    listener,
                    DiscordOAuthCallbackResult(code=code, state=state),
                )
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
