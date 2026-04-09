from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from uuid import uuid4

import numpy as np

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.runtime_logging import SessionRuntimeLoggingService
from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import SpeechEnd, SpeechStart
from puripuly_heart.domain.events import STTFinalEvent, STTSessionState, STTSessionStateEvent
from tests.helpers.fakes import samples


@dataclass(slots=True)
class _RuntimeLogSinks:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: object


def _make_runtime_logging_capture() -> tuple[SessionRuntimeLoggingService, io.StringIO]:
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger(f"test.stt.runtime.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    session_logger = logging.getLogger(f"test.stt.runtime.session.{uuid4()}")
    session_logger.handlers.clear()
    session_logger.propagate = False

    runtime_logging = SessionRuntimeLoggingService(
        root_logger=root_logger,
        session_logger=session_logger,
        sinks=_RuntimeLogSinks(
            stream_handler=stream_handler,
            file_handler=logging.NullHandler(),
            log_file="runtime.log",
        ),
    )
    return runtime_logging, stream


def _runtime_log_messages(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line]


@dataclass(slots=True)
class FakeSession:
    audio: list[bytes]
    _queue: asyncio.Queue
    calls: list[str]
    _closed: bool = False

    def __init__(self) -> None:
        self.audio = []
        self._queue = asyncio.Queue()
        self.calls = []

    async def send_audio(self, pcm16le: bytes) -> None:
        self.audio.append(pcm16le)
        if len(self.audio) == 1:
            await self._queue.put(STTBackendTranscriptEvent(text="partial", is_final=False))

    async def stop(self) -> None:
        self.calls.append("stop")
        await self._queue.put(STTBackendTranscriptEvent(text="final", is_final=True))
        await self._queue.put(None)  # sentinel

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        self.calls.append("on_speech_end")

    async def close(self) -> None:
        self._closed = True
        self.calls.append("close")

    async def events(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


@dataclass(slots=True)
class FakeBackend:
    sessions: list[FakeSession]

    def __init__(self) -> None:
        self.sessions = []

    async def open_session(self) -> FakeSession:
        s = FakeSession()
        self.sessions.append(s)
        return s


@dataclass(slots=True)
class EventOnlySession:
    items: list[object]

    async def send_audio(self, pcm16le: bytes) -> None:
        _ = pcm16le

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms

    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def events(self):
        for item in self.items:
            yield item


@dataclass(slots=True)
class EventOnlyBackend:
    session: object

    async def open_session(self):
        return self.session


@dataclass(slots=True)
class FailingSession:
    error: Exception
    audio: list[bytes]

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.audio = []

    async def send_audio(self, pcm16le: bytes) -> None:
        self.audio.append(pcm16le)

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms

    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def events(self):
        if False:
            yield None
        raise self.error


@dataclass(slots=True)
class FailingBackend:
    error: Exception

    async def open_session(self):
        return FailingSession(self.error)


@dataclass(slots=True)
class TerminalFailureSession:
    closed: bool = False
    stopped: bool = False

    async def send_audio(self, pcm16le: bytes) -> None:
        _ = pcm16le

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms

    async def stop(self) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.closed = True

    async def events(self):
        if False:
            yield STTBackendTranscriptEvent(text="", is_final=False)
        raise RuntimeError("backend closed")


@dataclass(slots=True)
class TerminalFailureBackend:
    sessions: list[TerminalFailureSession]

    def __init__(self) -> None:
        self.sessions = []

    async def open_session(self) -> TerminalFailureSession:
        session = TerminalFailureSession()
        self.sessions.append(session)
        return session


async def _next_event(stream, *, timeout_s: float = 0.2):
    return await asyncio.wait_for(stream.__anext__(), timeout=timeout_s)


async def _next_state(stream, state, *, max_events: int = 5):
    for _ in range(max_events):
        event = await _next_event(stream)
        if isinstance(event, STTSessionStateEvent) and event.state == state:
            return event
    raise AssertionError(f"Expected state {state}")


async def test_stt_controller_connects_on_speech_start():
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend, sample_rate_hz=16000, clock=clock, reset_deadline_s=90.0
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    first = await _next_state(stream, STTSessionState.STREAMING)

    assert len(backend.sessions) == 1
    assert isinstance(first, STTSessionStateEvent)
    assert first.state == STTSessionState.STREAMING

    await stt.close()


async def test_stt_controller_resets_with_bridging_during_speech():
    """Timer-based reset triggers bridging when speaking at deadline."""
    backend = FakeBackend()
    runtime_logging, log_stream = _make_runtime_logging_capture()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        drain_timeout_s=0.05,
        bridging_ms=64,
        finalize_grace_s=0.0,
        runtime_logging=runtime_logging,
    )

    try:
        uid = __import__("uuid").uuid4()
        stream = stt.events()
        await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
        _ = await _next_event(stream)

        # Wait for timer to fire while still speaking (utterance_id is set)
        await asyncio.sleep(0.15)

        assert len(backend.sessions) == 2
        assert len(backend.sessions[1].audio) >= 1  # bridging audio
        assert "on_speech_end" not in backend.sessions[0].calls

        messages = _runtime_log_messages(log_stream)
        assert "[STT] Session reset while speaking; bridged to a new session" in messages
        assert not any("BRIDGING:" in message for message in messages)
    finally:
        await stt.close()
        runtime_logging.close()


async def test_stt_controller_resets_on_silence():
    """Timer-based reset closes session when silent at deadline."""
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.0,  # Disable auto-reconnect -> silence reset
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # End speech before timer fires
    await stt.handle_vad_event(SpeechEnd(uid))

    # Wait for timer to fire during silence
    await asyncio.sleep(0.15)

    # Verify: session closed (DISCONNECTED state)
    assert stt.state == STTSessionState.DISCONNECTED
    assert len(backend.sessions) == 1  # No new session created

    await stt.close()


async def test_stt_controller_finalize_on_close_while_speaking():
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    await stt.close()

    calls = backend.sessions[0].calls
    assert "on_speech_end" in calls
    assert "stop" in calls
    assert calls.index("on_speech_end") < calls.index("stop")


async def test_stt_controller_reconnects_when_recent_speech():
    """Timer-based reset reconnects when recent speech at deadline."""
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.5,  # Enable auto-reconnect
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    # 1. Speech start -> session 1 opens
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)
    assert len(backend.sessions) == 1

    # 2. End speech before timer fires (sets _last_speech_end_time)
    await stt.handle_vad_event(SpeechEnd(uid))

    # 3. Wait for timer to fire while in "recent speech" window
    await asyncio.sleep(0.15)

    # 4. Verify: new session opened via reconnect (not silence reset)
    assert len(backend.sessions) == 2
    assert "on_speech_end" in backend.sessions[0].calls  # allow_finalize=True

    await stt.close()


async def test_stt_controller_disconnects_when_reconnect_disabled():
    """Timer-based reset with reconnect_window_s=0 -> silence reset (DISCONNECTED)"""
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.0,  # Disabled -> always silence reset
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    # 1. Speech start -> session opens
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # 2. End speech before timer fires
    await stt.handle_vad_event(SpeechEnd(uid))

    # 3. Wait for timer to fire - since reconnect_window_s=0, always silence reset
    await asyncio.sleep(0.15)

    # Verify: DISCONNECTED state, no new session
    assert stt.state == STTSessionState.DISCONNECTED
    assert len(backend.sessions) == 1  # No new session

    await stt.close()


async def test_stt_controller_reconnect_allows_finalize():
    """Timer-based reconnect drains old session with allow_finalize=True"""
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.5,  # Enable auto-reconnect
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # End speech, then wait for timer to trigger reconnect
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.15)

    # Verify: old session called on_speech_end (finalize via allow_finalize=True)
    old_session = backend.sessions[0]
    assert "on_speech_end" in old_session.calls
    assert "stop" in old_session.calls

    await stt.close()


async def test_stt_controller_reconnect_no_bridging_audio():
    """Timer-based reconnect should not send bridging audio to new session"""
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.5,  # Enable auto-reconnect
        bridging_ms=64,
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # End speech, then wait for timer to trigger reconnect
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.15)

    # Verify: new session has no bridging audio (unlike bridging reset)
    new_session = backend.sessions[1]
    assert len(new_session.audio) == 0

    await stt.close()


async def test_stt_controller_reconnect_fallback_on_failure():
    """Timer-based reconnect failure should fallback to silence reset"""

    class FailingBackend:
        def __init__(self):
            self.sessions = []
            self.call_count = 0

        async def open_session(self):
            self.call_count += 1
            if self.call_count == 1:
                s = FakeSession()
                self.sessions.append(s)
                return s
            raise ConnectionError("Failed to connect")

    backend = FailingBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=0.1,  # 100ms for fast test
        reconnect_window_s=0.5,  # Enable auto-reconnect
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
        connect_attempts=1,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # End speech, then wait for timer to trigger reconnect (which will fail)
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.15)

    # Verify: connection failure -> DISCONNECTED state (fallback to silence reset)
    assert stt.state == STTSessionState.DISCONNECTED

    await stt.close()


async def test_stt_controller_summarizes_retry_connect_in_basic_runtime_logs() -> None:
    class RetryOnceBackend:
        def __init__(self) -> None:
            self.attempts = 0

        async def open_session(self):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("temporary outage")
            return FakeSession()

    runtime_logging, log_stream = _make_runtime_logging_capture()
    stt = ManagedSTTProvider(
        backend=RetryOnceBackend(),
        sample_rate_hz=16000,
        clock=FakeClock(),
        connect_attempts=2,
        connect_retry_base_s=0.001,
        connect_retry_max_s=0.001,
        runtime_logging=runtime_logging,
    )

    try:
        stream = stt.events()
        await stt.handle_vad_event(SpeechStart(uuid4(), pre_roll=samples(0.0), chunk=samples(1.0)))
        await _next_state(stream, STTSessionState.STREAMING)

        messages = _runtime_log_messages(log_stream)
        assert "[STT] Session connected after 1 retry" in messages
        assert not any("Opening new session" in message for message in messages)
        assert not any("Retrying session in" in message for message in messages)
    finally:
        await stt.close()
        runtime_logging.close()


async def test_stt_controller_without_runtime_logging_stays_basic_only(caplog) -> None:
    class RetryOnceBackend:
        def __init__(self) -> None:
            self.attempts = 0

        async def open_session(self):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("temporary outage")
            return FakeSession()

    stt = ManagedSTTProvider(
        backend=RetryOnceBackend(),
        sample_rate_hz=16000,
        clock=FakeClock(),
        connect_attempts=2,
        connect_retry_base_s=0.001,
        connect_retry_max_s=0.001,
    )

    try:
        with caplog.at_level(logging.INFO, logger="puripuly_heart.core.stt.controller"):
            await stt.handle_vad_event(
                SpeechStart(uuid4(), pre_roll=samples(0.0), chunk=samples(1.0))
            )

        assert "[STT] Session connected after 1 retry" in caplog.messages
        assert not any("Opening new session" in message for message in caplog.messages)
        assert not any("Retrying session in" in message for message in caplog.messages)
    finally:
        await stt.close()


async def test_managed_stt_provider_peer_channel_produces_final_event():
    provider = ManagedSTTProvider(
        backend=FakeBackend(),
        sample_rate_hz=16000,
        channel="peer",
    )
    utterance_id = uuid4()
    provider._pending_final_utterance_id = utterance_id

    await provider._consume_session_events(
        EventOnlySession(
            items=[
                STTBackendTranscriptEvent(
                    text="peer line",
                    is_final=True,
                )
            ]
        ),
    )

    event = await _next_event(provider.events())
    assert isinstance(event, STTFinalEvent)
    assert event.transcript.channel == "peer"
    assert event.transcript.text == "peer line"


async def test_managed_stt_provider_skips_empty_audio_send() -> None:
    session = FakeSession()
    backend = EventOnlyBackend(session=session)
    stt = ManagedSTTProvider(backend=backend, sample_rate_hz=16000, channel="peer")

    uid = uuid4()
    await stt.handle_vad_event(
        SpeechStart(uid, pre_roll=np.zeros(0, dtype=np.float32), chunk=samples(1.0))
    )

    assert b"" not in session.audio


async def test_managed_stt_provider_invokes_terminal_failure_callback_after_consumer_error() -> (
    None
):
    errors: list[str] = []
    backend = FailingBackend(RuntimeError("closed"))
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        channel="peer",
        connect_attempts=1,
        on_terminal_failure=lambda exc: errors.append(str(exc)),
    )

    uid = uuid4()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0)

    assert stt.state == STTSessionState.DISCONNECTED
    assert stt._active_session is None
    assert errors == ["closed"]


async def test_stt_controller_closes_failed_session_after_consumer_error() -> None:
    backend = TerminalFailureBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        reset_deadline_s=90.0,
        drain_timeout_s=0.05,
    )

    uid = uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    await asyncio.sleep(0.01)

    assert stt.state == STTSessionState.DISCONNECTED
    assert stt._active_session is None
    assert stt._consumer_task is None
    assert backend.sessions[0].closed is True

    await stt.close()
