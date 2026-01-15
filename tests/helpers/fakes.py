from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np

from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent


@dataclass(slots=True)
class FakeSender:
    sent: list[str]
    typing: list[bool]

    def __init__(self) -> None:
        self.sent = []
        self.typing = []

    def send_chatbox(self, text: str) -> None:
        self.sent.append(text)

    def send_typing(self, is_typing: bool) -> None:
        self.typing.append(is_typing)


@dataclass(slots=True)
class SpeechAwareFakeSession:
    audio: list[bytes]
    _queue: asyncio.Queue
    _seen_speech: bool = False

    def __init__(self) -> None:
        self.audio = []
        self._queue = asyncio.Queue()
        self._seen_speech = False

    async def send_audio(self, pcm16le: bytes) -> None:
        self.audio.append(pcm16le)
        is_silence = all(b == 0 for b in pcm16le)
        if not is_silence:
            self._seen_speech = True
            await self._queue.put(STTBackendTranscriptEvent(text="PARTIAL", is_final=False))
        elif self._seen_speech:
            await self._queue.put(STTBackendTranscriptEvent(text="FINAL", is_final=True))
            self._seen_speech = False

    async def on_speech_end(self) -> None:
        if self._seen_speech:
            await self._queue.put(STTBackendTranscriptEvent(text="FINAL", is_final=True))
            self._seen_speech = False

    async def stop(self) -> None:
        await self._queue.put(None)

    async def close(self) -> None:
        await self._queue.put(None)

    async def events(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


@dataclass(slots=True)
class SpeechAwareFakeBackend:
    async def open_session(self) -> SpeechAwareFakeSession:
        return SpeechAwareFakeSession()


def samples(value: float, n: int = 512) -> np.ndarray:
    return np.full((n,), value, dtype=np.float32)
