from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from puripuly_heart.config.paths import user_config_dir

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
MAIN_LOG_FILENAME = "puripuly_heart.log"
_MAIN_STREAM_HANDLER_NAME = "puripuly_heart.main.stream"
_MAIN_FILE_HANDLER_NAME = "puripuly_heart.main.file"
_SESSION_LOGGER_NAME = "puripuly_heart.runtime.session"


class RealtimeLogSink(Protocol):
    def append_log(self, line: str) -> None: ...


class SessionLoggingMode(str, Enum):
    BASIC = "basic"
    DETAILED = "detailed"


@dataclass(frozen=True, slots=True)
class RuntimeLoggingSinks:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: Path


def default_main_log_file(*, log_dir: Path | None = None) -> Path:
    resolved_log_dir = log_dir or user_config_dir()
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    return resolved_log_dir / MAIN_LOG_FILENAME


def configure_main_logging(
    *,
    root_logger: logging.Logger | None = None,
    log_dir: Path | None = None,
) -> RuntimeLoggingSinks:
    target_logger = root_logger or logging.getLogger()
    log_file = default_main_log_file(log_dir=log_dir)

    stream_handler = _find_main_stream_handler(target_logger)
    if stream_handler is None:
        stream_handler = logging.StreamHandler()
        stream_handler.set_name(_MAIN_STREAM_HANDLER_NAME)
        stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        target_logger.addHandler(stream_handler)

    file_handler = _find_main_file_handler(target_logger, log_file=log_file)
    if file_handler is None:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=0,
            encoding="utf-8",
        )
        file_handler.set_name(_MAIN_FILE_HANDLER_NAME)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        target_logger.addHandler(file_handler)

    target_logger.setLevel(logging.INFO)
    return RuntimeLoggingSinks(
        stream_handler=stream_handler,
        file_handler=file_handler,
        log_file=log_file,
    )


class SessionRuntimeLoggingService:
    def __init__(
        self,
        *,
        root_logger: logging.Logger | None = None,
        session_logger: logging.Logger | None = None,
        sinks: RuntimeLoggingSinks | None = None,
        ui_handler_factory: Callable[[RealtimeLogSink], logging.Handler] | None = None,
    ) -> None:
        self._root_logger = root_logger or logging.getLogger()
        self._sinks = sinks or configure_main_logging(root_logger=self._root_logger)
        self._session_logger = session_logger or logging.getLogger(_new_session_logger_name())
        self._root_logger.setLevel(logging.INFO)
        self._session_logger.setLevel(logging.INFO)
        self._session_logger.propagate = False
        self._ui_handler_factory = ui_handler_factory
        self._realtime_sink: RealtimeLogSink | None = None
        self._ui_handler: logging.Handler | None = None
        self._session_handlers: list[logging.Handler] = []
        self._mode = SessionLoggingMode.BASIC

        _ensure_handler(self._root_logger, self._sinks.stream_handler)
        _ensure_handler(self._root_logger, self._sinks.file_handler)
        if _ensure_handler(self._session_logger, self._sinks.stream_handler):
            self._session_handlers.append(self._sinks.stream_handler)
        if _ensure_handler(self._session_logger, self._sinks.file_handler):
            self._session_handlers.append(self._sinks.file_handler)

    @property
    def mode(self) -> SessionLoggingMode:
        return self._mode

    @property
    def log_file(self) -> Path:
        return self._sinks.log_file

    def set_mode(self, mode: SessionLoggingMode | str) -> None:
        self._mode = SessionLoggingMode(mode)

    def attach_realtime_sink(self, sink: RealtimeLogSink) -> None:
        if self._realtime_sink is sink:
            return

        self.detach_realtime_sink()
        self._realtime_sink = sink
        if self._ui_handler_factory is None:
            return

        handler = self._ui_handler_factory(sink)
        self._ui_handler = handler
        _ensure_handler(self._root_logger, handler)
        _ensure_handler(self._session_logger, handler)

    def detach_realtime_sink(self) -> None:
        if self._ui_handler is not None:
            with contextlib.suppress(Exception):
                self._root_logger.removeHandler(self._ui_handler)
            with contextlib.suppress(Exception):
                self._session_logger.removeHandler(self._ui_handler)
            with contextlib.suppress(Exception):
                self._ui_handler.close()
        self._realtime_sink = None
        self._ui_handler = None

    def emit_basic(self, message: str, *, level: int = logging.INFO) -> None:
        self._session_logger.log(level, message)

    def emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if self._mode is not SessionLoggingMode.DETAILED:
            return False
        self._session_logger.log(level, message)
        return True

    def close(self) -> None:
        self.detach_realtime_sink()
        for handler in self._session_handlers:
            with contextlib.suppress(Exception):
                self._session_logger.removeHandler(handler)
        self._session_handlers.clear()


def _ensure_handler(logger: logging.Logger, handler: logging.Handler) -> bool:
    if handler not in logger.handlers:
        logger.addHandler(handler)
        return True
    return False


def _new_session_logger_name() -> str:
    return f"{_SESSION_LOGGER_NAME}.{uuid4()}"


def _find_main_stream_handler(logger: logging.Logger) -> logging.Handler | None:
    fallback: logging.Handler | None = None
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            if handler.get_name() == _MAIN_STREAM_HANDLER_NAME:
                return handler
            fallback = fallback or handler
    if fallback is not None:
        fallback.set_name(_MAIN_STREAM_HANDLER_NAME)
    return fallback


def _find_main_file_handler(logger: logging.Logger, *, log_file: Path) -> logging.Handler | None:
    expected_path = str(log_file.resolve())
    for handler in logger.handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        if handler.get_name() == _MAIN_FILE_HANDLER_NAME:
            return handler
        if str(Path(handler.baseFilename).resolve()) == expected_path:
            handler.set_name(_MAIN_FILE_HANDLER_NAME)
            return handler
    return None
