from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.providers.stt import qwen_asr as qwen_asr_module
from puripuly_heart.providers.stt.qwen_asr import _COMMIT, _STOP, _QwenASRSession


def _make_session() -> _QwenASRSession:
    return _QwenASRSession(
        api_key="k",
        model="m",
        language="en",
        endpoint="wss://example",
        sample_rate_hz=16000,
        connect_timeout_s=5.0,
    )


@pytest.mark.asyncio
async def test_qwen_asr_session_on_speech_end_enqueues_commit(monkeypatch):
    async def fake_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    session = _make_session()
    await session.on_speech_end()

    silence = session._audio_q.get_nowait()
    commit = session._audio_q.get_nowait()

    assert isinstance(silence, bytes)
    assert commit is _COMMIT


@pytest.mark.asyncio
async def test_qwen_asr_session_send_audio_and_stop() -> None:
    session = _make_session()

    await session.send_audio(b"abc")
    assert session._audio_q.get_nowait() == b"abc"

    await session.stop()
    assert session._stopped is True
    assert session._audio_q.get_nowait() is _STOP


@pytest.mark.asyncio
async def test_qwen_asr_session_reports_error(monkeypatch) -> None:
    session = _make_session()
    session._loop = asyncio.get_running_loop()

    err = RuntimeError("boom")
    session._report_error(err)
    await asyncio.sleep(0)

    event = await session._events.get()
    assert event is err
    assert session._error_reported is True
    assert session._connect_error is err
    assert session._connected.is_set() is True


@pytest.mark.asyncio
async def test_qwen_asr_session_events_yield_and_raise() -> None:
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
async def test_qwen_asr_session_start_success(monkeypatch) -> None:
    session = _make_session()

    class DummyThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

        def join(self, timeout=None):
            return None

    def fake_run_sync():
        session._connected.set()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(qwen_asr_module.threading, "Thread", DummyThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    await session.start()
    assert session._connected.is_set() is True


@pytest.mark.asyncio
async def test_qwen_asr_session_start_failure(monkeypatch) -> None:
    session = _make_session()

    class DummyThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

        def join(self, timeout=None):
            return None

    def fake_run_sync():
        session._connect_error = RuntimeError("fail")
        session._connected.set()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(qwen_asr_module.threading, "Thread", DummyThread)
    monkeypatch.setattr(session, "_run_sync", fake_run_sync)

    with pytest.raises(RuntimeError, match="fail"):
        await session.start()
