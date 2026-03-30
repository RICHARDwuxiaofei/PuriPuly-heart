from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTIntegratedFinalEvent,
    STTPartialEvent,
    STTSessionStateEvent,
)
from puripuly_heart.providers.live.gemini_live import GeminiLiveIntegratedProvider


class FakeLiveSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self._batches: asyncio.Queue[list[object] | Exception | None] = asyncio.Queue()
        self.closed = False

    async def send_realtime_input(self, **kwargs) -> None:
        self.sent.append(kwargs)

    async def push_batch(self, *messages: object) -> None:
        await self._batches.put(list(messages))

    async def fail_receive(self, exc: Exception) -> None:
        await self._batches.put(exc)

    async def receive(self):
        batch = await self._batches.get()
        if batch is None:
            return
        if isinstance(batch, Exception):
            raise batch
        for message in batch:
            yield message

    async def close(self) -> None:
        self.closed = True
        await self._batches.put(None)


class FakeLiveConnect:
    def __init__(self, session: FakeLiveSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeLiveSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.session.close()


class FakeClient:
    def __init__(self, sessions: list[FakeLiveSession]) -> None:
        self.sessions = sessions
        self.connect_calls: list[tuple[str, object | None]] = []
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self.connect))

    def connect(self, *, model: str, config=None):
        self.connect_calls.append((model, config))
        return FakeLiveConnect(self.sessions[len(self.connect_calls) - 1])


def _pcm(shape: int, value: float) -> np.ndarray:
    return np.full(shape, value, dtype=np.float32)


async def _next_relevant_event(provider: GeminiLiveIntegratedProvider):
    stream = provider.events()
    while True:
        event = await asyncio.wait_for(anext(stream), timeout=1.0)
        if isinstance(event, STTSessionStateEvent):
            continue
        return event


@pytest.mark.asyncio
async def test_gemini_live_provider_streams_audio_and_emits_integrated_final() -> None:
    session = FakeLiveSession()
    client = FakeClient([session])
    provider = GeminiLiveIntegratedProvider(
        api_key="k",
        sample_rate_hz=16000,
        source_language="ko",
        target_language="en",
        system_prompt="Translate ${sourceName} to ${targetName}.",
        clock=FakeClock(),
        client=client,
    )
    utterance_id = uuid4()

    await provider.handle_vad_event(
        SpeechStart(utterance_id=utterance_id, pre_roll=_pcm(4, 0.05), chunk=_pcm(4, 0.1))
    )
    await provider.handle_vad_event(SpeechChunk(utterance_id=utterance_id, chunk=_pcm(4, 0.2)))
    await provider.handle_vad_event(SpeechEnd(utterance_id=utterance_id))

    await session.push_batch(
        SimpleNamespace(
            session_resumption_update=SimpleNamespace(new_handle="handle-1", resumable=True),
            server_content=None,
            go_away=None,
        ),
        SimpleNamespace(
            session_resumption_update=None,
            go_away=None,
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="안녕"),
                output_transcription=SimpleNamespace(text="hello"),
                model_turn=SimpleNamespace(
                    parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"ignored"))]
                ),
                turn_complete=False,
                interrupted=False,
            ),
        ),
        SimpleNamespace(
            session_resumption_update=None,
            go_away=None,
            server_content=SimpleNamespace(
                input_transcription=None,
                output_transcription=SimpleNamespace(text="hello"),
                model_turn=None,
                turn_complete=True,
                interrupted=False,
            ),
        ),
    )

    partial = await _next_relevant_event(provider)
    final = await _next_relevant_event(provider)

    assert isinstance(partial, STTPartialEvent)
    assert partial.transcript.text == "안녕"
    assert isinstance(final, STTIntegratedFinalEvent)
    assert final.utterance_id == utterance_id
    assert final.transcript_text == "안녕"
    assert final.translation_text == "hello"
    assert provider._resumption_handle == "handle-1"
    assert [tuple(sorted(item.keys())) for item in session.sent] == [
        ("activity_start",),
        ("audio",),
        ("audio",),
        ("audio",),
        ("activity_end",),
    ]

    await provider.close()


@pytest.mark.asyncio
async def test_gemini_live_provider_emits_error_and_reconnects_on_next_turn() -> None:
    first = FakeLiveSession()
    second = FakeLiveSession()
    client = FakeClient([first, second])
    provider = GeminiLiveIntegratedProvider(
        api_key="k",
        sample_rate_hz=16000,
        source_language="ko",
        target_language="en",
        system_prompt="Translate",
        clock=FakeClock(),
        client=client,
    )
    utterance_id = uuid4()

    await provider.handle_vad_event(
        SpeechStart(utterance_id=utterance_id, pre_roll=_pcm(2, 0.05), chunk=_pcm(2, 0.1))
    )
    await first.fail_receive(RuntimeError("socket lost"))

    event = await _next_relevant_event(provider)
    assert isinstance(event, STTErrorEvent)
    assert event.utterance_id == utterance_id

    second_utterance = uuid4()
    await provider.handle_vad_event(
        SpeechStart(
            utterance_id=second_utterance,
            pre_roll=_pcm(2, 0.05),
            chunk=_pcm(2, 0.1),
        )
    )

    assert len(client.connect_calls) == 2
    assert second.sent[0].get("activity_start") is not None

    await provider.close()
