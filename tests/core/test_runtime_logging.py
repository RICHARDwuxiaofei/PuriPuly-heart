from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from puripuly_heart.core.runtime_logging import (
    SessionLoggingMode,
    SessionRuntimeLoggingService,
    configure_main_logging,
)


@dataclass
class _SharedSinkBundle:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: object


def _format_with_handler(handler: logging.Handler) -> str:
    record = logging.LogRecord(
        name="test.runtime",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = 0.0
    record.msecs = 123.0
    return handler.format(record)


def _make_runtime_logging_capture() -> tuple[SessionRuntimeLoggingService, io.StringIO]:
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)

    root_logger = logging.getLogger(f"test.runtime_logging.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    session_logger = logging.getLogger(f"test.runtime_logging.session.{uuid4()}")
    session_logger.handlers.clear()
    session_logger.propagate = False

    runtime_logging = SessionRuntimeLoggingService(
        root_logger=root_logger,
        session_logger=session_logger,
        sinks=_SharedSinkBundle(
            stream_handler=stream_handler,
            file_handler=logging.NullHandler(),
            log_file="runtime.log",
        ),
    )
    return runtime_logging, stream


def test_configure_main_logging_formats_new_handlers_with_millisecond_resolution(tmp_path) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.configure.new.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    formatted_stream = _format_with_handler(sinks.stream_handler)
    formatted_file = _format_with_handler(sinks.file_handler)

    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello", formatted_stream)
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello", formatted_file)


def test_configure_main_logging_reused_handlers_get_millisecond_resolution_formatter(
    tmp_path,
) -> None:
    root_logger = logging.getLogger(f"test.runtime_logging.configure.reused.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    existing_stream = logging.StreamHandler(io.StringIO())
    existing_stream.setFormatter(logging.Formatter("%(message)s"))
    existing_file = RotatingFileHandler(
        tmp_path / "puripuly_heart.log",
        maxBytes=4096,
        backupCount=0,
        encoding="utf-8",
    )
    existing_file.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(existing_stream)
    root_logger.addHandler(existing_file)

    sinks = configure_main_logging(root_logger=root_logger, log_dir=tmp_path)

    assert sinks.stream_handler is existing_stream
    assert sinks.file_handler is existing_file
    assert re.fullmatch(
        r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello",
        _format_with_handler(existing_stream),
    )
    assert re.fullmatch(
        r"\d{2}:\d{2}:\d{2}\.123 \[INFO\] test\.runtime: hello",
        _format_with_handler(existing_file),
    )


def test_emit_detailed_lazy_checks_mode_before_formatting() -> None:
    runtime_logging, stream = _make_runtime_logging_capture()
    builder_calls = 0

    def builder() -> str:
        nonlocal builder_calls
        builder_calls += 1
        return "lazy detail"

    try:
        assert runtime_logging.emit_detailed_lazy(builder) is False
        assert builder_calls == 0
        assert stream.getvalue() == ""

        runtime_logging.set_mode(SessionLoggingMode.DETAILED)

        assert runtime_logging.emit_detailed_lazy(builder) is True
        assert builder_calls == 1
        assert stream.getvalue().splitlines() == ["lazy detail"]
    finally:
        runtime_logging.close()
