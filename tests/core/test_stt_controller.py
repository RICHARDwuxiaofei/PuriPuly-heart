from __future__ import annotations

import asyncio
from dataclasses import dataclass

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.events import STTSessionState, STTSessionStateEvent
from tests.helpers.fakes import samples


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

    async def on_speech_end(self) -> None:
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
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        drain_timeout_s=0.05,
        bridging_ms=64,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    _ = await _next_event(stream)

    clock.advance(1.1)
    await stt.handle_vad_event(SpeechChunk(uid, chunk=samples(2.0)))

    await asyncio.sleep(0.01)
    assert len(backend.sessions) == 2
    assert len(backend.sessions[1].audio) >= 1  # bridging audio
    assert "on_speech_end" not in backend.sessions[0].calls

    await stt.close()


async def test_stt_controller_resets_on_silence():
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.0,  # Disable auto-reconnect for legacy behavior
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    _ = await _next_event(stream)

    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))

    seen_disconnected = False
    for _ in range(10):
        ev = await _next_event(stream)
        if isinstance(ev, STTSessionStateEvent) and ev.state == STTSessionState.DISCONNECTED:
            seen_disconnected = True
            break
    assert seen_disconnected, "Expected DISCONNECTED state event"

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
    """Session limit + recent speech -> immediate reconnect (STREAMING maintained)"""
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.5,
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    # 1. Speech start -> session 1 opens
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)
    assert len(backend.sessions) == 1

    # 2. Session limit exceeded, then SpeechEnd triggers _maybe_reset
    # Since SpeechEnd just happened, _has_recent_speech() returns True -> reconnect
    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.01)

    # 3. Verify: new session opened, no DISCONNECTED (reconnect keeps STREAMING)
    assert len(backend.sessions) == 2
    assert "on_speech_end" in backend.sessions[0].calls  # allow_finalize=True

    await stt.close()


async def test_stt_controller_disconnects_when_reconnect_disabled():
    """reconnect_window_s=0 disables auto-reconnect -> silence reset (DISCONNECTED)"""
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.0,  # Disabled
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    # 1. Speech start -> session opens
    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # 2. Session limit exceeded, SpeechEnd triggers _maybe_reset
    # reconnect_window_s=0 means _has_recent_speech() always False -> silence reset
    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))

    # 3. Verify: DISCONNECTED state
    seen_disconnected = False
    for _ in range(10):
        ev = await _next_event(stream)
        if isinstance(ev, STTSessionStateEvent) and ev.state == STTSessionState.DISCONNECTED:
            seen_disconnected = True
            break
    assert seen_disconnected
    assert len(backend.sessions) == 1  # No new session

    await stt.close()


async def test_stt_controller_reconnect_allows_finalize():
    """Reconnect should drain old session with allow_finalize=True"""
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.5,
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # Session limit exceeded, SpeechEnd triggers reconnect
    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.01)

    # Verify: old session called on_speech_end (finalize via allow_finalize=True)
    old_session = backend.sessions[0]
    assert "on_speech_end" in old_session.calls
    assert "stop" in old_session.calls

    await stt.close()


async def test_stt_controller_reconnect_no_bridging_audio():
    """Reconnect should not send bridging audio to new session"""
    clock = FakeClock()
    backend = FakeBackend()
    stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.5,
        bridging_ms=64,
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # Session limit exceeded, SpeechEnd triggers reconnect
    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.01)

    # Verify: new session has no bridging audio (unlike bridging reset)
    new_session = backend.sessions[1]
    assert len(new_session.audio) == 0

    await stt.close()


async def test_stt_controller_reconnect_fallback_on_failure():
    """Reconnect failure should fallback to silence reset"""
    clock = FakeClock()

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
        clock=clock,
        reset_deadline_s=1.0,
        reconnect_window_s=0.5,
        drain_timeout_s=0.05,
        finalize_grace_s=0.0,
        connect_attempts=1,
    )

    uid = __import__("uuid").uuid4()
    stream = stt.events()

    await stt.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await _next_state(stream, STTSessionState.STREAMING)

    # Session limit exceeded, SpeechEnd triggers reconnect but it fails
    clock.advance(1.1)
    await stt.handle_vad_event(SpeechEnd(uid))
    await asyncio.sleep(0.01)

    # Verify: connection failure -> DISCONNECTED state (fallback to silence reset)
    seen_disconnected = False
    for _ in range(10):
        ev = await _next_event(stream)
        if isinstance(ev, STTSessionStateEvent) and ev.state == STTSessionState.DISCONNECTED:
            seen_disconnected = True
            break
    assert seen_disconnected

    await stt.close()
