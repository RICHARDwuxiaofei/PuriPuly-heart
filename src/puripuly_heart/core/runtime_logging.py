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


@dataclass(frozen=True, slots=True)
class LatencyTracePointContract:
    name: str
    timing_semantics: str
    acceptance_expectation: str


LATENCY_TRACE_POINT_CONTRACTS: dict[str, LatencyTracePointContract] = {
    "speech_end": LatencyTracePointContract(
        name="speech_end",
        timing_semantics="Shared latency zero boundary recorded when the hub accepts SpeechEnd for the utterance.",
        acceptance_expectation="Use the post-VAD SpeechEnd boundary only; do not add hangover_s back into published latency values.",
    ),
    "stt_final": LatencyTracePointContract(
        name="stt_final",
        timing_semantics="Recorded when the hub accepts the final STT transcript that will feed the final output path.",
        acceptance_expectation="Emit at most once per output path using the final transcript text that survives to output publication.",
    ),
    "llm_request_start": LatencyTracePointContract(
        name="llm_request_start",
        timing_semantics="Recorded immediately before the hub calls the translation provider for the output path.",
        acceptance_expectation="Use the request that contributes to the published output, not cancelled exploratory retries.",
    ),
    "llm_first_chunk": LatencyTracePointContract(
        name="llm_first_chunk",
        timing_semantics="Recorded when the hub receives the first streaming translation chunk for the output path.",
        acceptance_expectation="Emit only for streaming paths and only on the first chunk that belongs to the published output.",
    ),
    "llm_done": LatencyTracePointContract(
        name="llm_done",
        timing_semantics="Recorded when the hub has the completed translation text ready for publication.",
        acceptance_expectation="Use the completed translation that is about to be published, whether it came from a streaming or non-streaming provider.",
    ),
    "self_chatbox_enqueue": LatencyTracePointContract(
        name="self_chatbox_enqueue",
        timing_semantics="Recorded when the hub enqueues the final self output into SmartOscQueue.",
        acceptance_expectation="This is the official self Basic latency end boundary because it is the final self output handoff point owned by the hub.",
    ),
    "peer_overlay_first_emit": LatencyTracePointContract(
        name="peer_overlay_first_emit",
        timing_semantics="Recorded at the first hub emission of final peer overlay output.",
        acceptance_expectation="Use the first overlay_sink.emit call that carries the completed peer translation payload for that utterance.",
    ),
    "peer_overlay_first_render": LatencyTracePointContract(
        name="peer_overlay_first_render",
        timing_semantics="Recorded by the downstream overlay when final peer overlay output first becomes visible.",
        acceptance_expectation="Emit once per utterance after peer_overlay_first_emit when the downstream overlay first renders the completed peer translation on screen.",
    ),
}


def format_basic_latency_summary(
    *,
    channel: str,
    e2e_ms: int,
    final_output_stage: str,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    parts.append(f"final_output_stage={final_output_stage}")
    return f"[Basic][Latency] {' '.join(parts)}"


def format_detailed_latency_trace(
    *,
    channel: str,
    utterance_id: str,
    stage: str,
    elapsed_ms: int,
) -> str:
    return (
        f"[Detailed][Latency] channel={channel} utterance_id={utterance_id} "
        f"stage={stage} elapsed_ms={elapsed_ms}"
    )


def format_detailed_latency_breakdown(
    *,
    channel: str,
    e2e_ms: int,
    final_output_stage: str,
    speech_end_to_stt_final_ms: int | None = None,
    stt_final_to_final_output_ms: int | None = None,
) -> str:
    parts = [
        f"channel={channel}",
        f"e2e_ms={e2e_ms}",
    ]
    if speech_end_to_stt_final_ms is not None:
        parts.append(f"speech_end_to_stt_final_ms={speech_end_to_stt_final_ms}")
    if stt_final_to_final_output_ms is not None:
        parts.append(f"stt_final_to_final_output_ms={stt_final_to_final_output_ms}")
    parts.append(f"final_output_stage={final_output_stage}")
    return f"[Detailed][LatencyBreakdown] {' '.join(parts)}"


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
