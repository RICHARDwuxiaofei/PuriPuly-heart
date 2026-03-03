from __future__ import annotations

import asyncio
import sys
import types

import pytest

from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.providers.stt import deepgram as deepgram_module
from puripuly_heart.providers.stt.deepgram import _FINALIZE, _STOP, _DeepgramSDKSession
from tests.helpers.fakes import NoopThread, TargetThread


def _make_session() -> _DeepgramSDKSession:
    return _DeepgramSDKSession(
        api_key="k",
        model="m",
        language="en",
        sample_rate_hz=16000,
        connect_timeout_s=5.0,
    )


@pytest.mark.asyncio
async def test_deepgram_session_on_speech_end_enqueues_finalize():
    session = _make_session()

    await session.on_speech_end(trailing_silence_ms=200)
    finalize = session._audio_q.get_nowait()
    assert finalize is _FINALIZE

    await session.on_speech_end(trailing_silence_ms=0)
    silence = session._audio_q.get_nowait()
    finalize = session._audio_q.get_nowait()

    assert isinstance(silence, bytes)
    assert finalize is _FINALIZE


@pytest.mark.asyncio
async def test_deepgram_session_send_audio_and_stop() -> None:
    session = _make_session()

    await session.send_audio(b"abc")
    assert session._audio_q.get_nowait() == b"abc"

    await session.stop()
    assert session._stopped is True
    assert session._audio_q.get_nowait() is _STOP


@pytest.mark.asyncio
async def test_deepgram_session_events_yield_and_raise() -> None:
    session = _make_session()

    session._events.put_nowait(STTBackendTranscriptEvent(text="hi", is_final=True))
    session._events.put_nowait(None)

    gen = session.events()
    event = await gen.__anext__()
    assert event.text == "hi"
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    session._events.put_nowait(RuntimeError("boom"))
    gen = session.events()
    with pytest.raises(RuntimeError, match="boom"):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_deepgram_session_start_success(monkeypatch) -> None:
    session = _make_session()

    def fake_run_sync():
        session._connected.set()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(deepgram_module.threading, "Thread", TargetThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    await session.start()
    assert session._connected.is_set() is True


@pytest.mark.asyncio
async def test_deepgram_session_start_timeout(monkeypatch) -> None:
    session = _make_session()

    def fake_run_sync():
        return None

    async def fake_to_thread(*_args, **_kwargs):
        return False

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(deepgram_module.threading, "Thread", TargetThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    with pytest.raises(RuntimeError, match="connection timeout"):
        await session.start()


@pytest.mark.asyncio
async def test_deepgram_session_report_error_is_emitted_once() -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()

    err = RuntimeError("boom")
    session._report_error(err)
    session._report_error(RuntimeError("second"))
    await asyncio.sleep(0)

    assert session._error_reported is True
    assert await session._events.get() is err
    assert session._events.empty()


@pytest.mark.asyncio
async def test_deepgram_session_run_sync_handles_message_finalize_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()
    session._connect_started_at = 1.0

    sent_media: list[bytes] = []
    sent_controls: list[str] = []

    class FakeEventType:
        OPEN = "open"
        MESSAGE = "message"
        ERROR = "error"
        CLOSE = "close"

    class FakeControlMessage:
        def __init__(self, type: str):
            self.type = type

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def on(self, event_type, callback):
            if event_type == FakeEventType.OPEN:
                callback(object())
            if event_type == FakeEventType.MESSAGE:
                alt = types.SimpleNamespace(transcript="hello world")
                result = types.SimpleNamespace(
                    channel=types.SimpleNamespace(alternatives=[alt]),
                    is_final=True,
                    speech_final=False,
                )
                callback(result)

        def start_listening(self):
            return None

        def send_control(self, message):
            sent_controls.append(message.type)

        def send_media(self, data: bytes):
            sent_media.append(data)

    class FakeV1:
        def connect(self, **_kwargs):
            return FakeConnection()

    class FakeListen:
        v1 = FakeV1()

    class FakeClient:
        def __init__(self, api_key: str):
            _ = api_key
            self.listen = FakeListen()

    deepgram_pkg = types.ModuleType("deepgram")
    deepgram_pkg.DeepgramClient = FakeClient
    deepgram_core = types.ModuleType("deepgram.core")
    deepgram_events = types.ModuleType("deepgram.core.events")
    deepgram_events.EventType = FakeEventType
    deepgram_ext = types.ModuleType("deepgram.extensions")
    deepgram_ext_types = types.ModuleType("deepgram.extensions.types")
    deepgram_sockets = types.ModuleType("deepgram.extensions.types.sockets")
    deepgram_sockets.ListenV1ControlMessage = FakeControlMessage

    monkeypatch.setitem(sys.modules, "deepgram", deepgram_pkg)
    monkeypatch.setitem(sys.modules, "deepgram.core", deepgram_core)
    monkeypatch.setitem(sys.modules, "deepgram.core.events", deepgram_events)
    monkeypatch.setitem(sys.modules, "deepgram.extensions", deepgram_ext)
    monkeypatch.setitem(sys.modules, "deepgram.extensions.types", deepgram_ext_types)
    monkeypatch.setitem(sys.modules, "deepgram.extensions.types.sockets", deepgram_sockets)

    monkeypatch.setattr(deepgram_module.threading, "Thread", NoopThread)

    session._audio_q.put_nowait(_FINALIZE)
    session._audio_q.put_nowait(b"pcm")
    session._audio_q.put_nowait(_STOP)
    session._run_sync()
    await asyncio.sleep(0)

    first = await session._events.get()
    assert isinstance(first, STTBackendTranscriptEvent)
    assert first.text == "hello world"
    assert sent_controls == ["Finalize"]
    assert sent_media == [b"pcm"]
    assert session._connected.is_set() is True

    # _run_sync posts termination markers in stop path/finally.
    tail: list[object] = []
    while not session._events.empty():
        tail.append(session._events.get_nowait())
    assert None in tail
