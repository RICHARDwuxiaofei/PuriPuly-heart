from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import SpeechEnd, SpeechStart
from puripuly_heart.domain.events import UIEventType
from puripuly_heart.domain.models import Translation
from tests.helpers.fakes import RecordingOscQueue, samples


@dataclass(slots=True)
class FakePeerSession:
    audio: list[bytes] = field(default_factory=list)
    _queue: asyncio.Queue[object | None] = field(default_factory=asyncio.Queue)
    _seen_speech: bool = False

    async def send_audio(self, pcm16le: bytes) -> None:
        self.audio.append(pcm16le)
        if any(byte != 0 for byte in pcm16le):
            self._seen_speech = True

    async def on_speech_end(self, *, trailing_silence_ms: int | None = None) -> None:
        _ = trailing_silence_ms
        if self._seen_speech:
            self._seen_speech = False
            await self._queue.put(STTBackendTranscriptEvent(text="peer final", is_final=True))

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
class FakePeerBackend:
    sessions: list[FakePeerSession] = field(default_factory=list)

    async def open_session(self) -> FakePeerSession:
        session = FakePeerSession()
        self.sessions.append(session)
        return session


@dataclass(slots=True)
class FakeLLM:
    calls: list[str] = field(default_factory=list)

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        _ = (utterance_id, system_prompt, source_language, target_language, context)
        self.calls.append(text)
        return Translation(utterance_id=utterance_id, text="translated")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_peer_desktop_transcripts_are_routed_to_peer_runtime_and_never_sent_to_chatbox() -> (
    None
):
    osc = RecordingOscQueue()
    hub = ClientHub(stt=None, llm=None, osc=osc, clock=FakeClock(_now=10.0))

    utterance_id = await hub.handle_peer_transcript_final_for_test(
        text="peer line",
        speaker_label="Speaker 0",
    )

    bundle = hub.get_or_create_bundle(utterance_id, channel="peer")
    event = await hub.ui_events.get()

    assert bundle.final is not None
    assert bundle.final.channel == "peer"
    assert bundle.final.text == "peer line"
    assert osc.messages == []
    assert event.type == UIEventType.TRANSCRIPT_FINAL
    assert event.channel == "peer"


@pytest.mark.asyncio
async def test_peer_session_reset_increments_epoch_and_integrated_context_ignores_old_peer_epoch() -> (
    None
):
    clock = FakeClock(_now=112.0)
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=clock,
        integrated_context_enabled=True,
        peer_translation_enabled=True,
    )
    hub.source_language = "en"
    hub.target_language = "ko"
    hub.self_runtime.remember_context(
        "self line",
        timestamp=100.0,
        source_language="en",
        target_language="ko",
    )
    hub.peer_runtime.peer_epoch = 2
    hub.peer_runtime.remember_context(
        "old peer line",
        timestamp=105.0,
        source_language="en",
        target_language="ko",
        speaker_label="Speaker 0",
        peer_epoch=2,
    )

    hub.reset_peer_session_for_test()

    context, mode = hub.context_resolver.resolve_for_request(
        runtime=hub.self_runtime,
        other_runtime=hub.peer_runtime,
        requested_mode="integrated",
        peer_translation_enabled=True,
        source_language="en",
        target_language="ko",
        expected_peer_epoch=hub.peer_runtime.peer_epoch,
    )

    assert hub.peer_runtime.peer_epoch == 3
    assert mode == "local"
    assert context == '- [12s ago] "self line"'
    assert "old peer line" not in context


@pytest.mark.asyncio
async def test_peer_epoch_only_changes_when_a_new_provider_session_opens() -> None:
    clock = FakeClock(_now=10.0)
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=clock)
    backend = FakePeerBackend()
    peer_stt = ManagedSTTProvider(
        backend=backend,
        sample_rate_hz=16000,
        channel="peer",
        peer_epoch_resolver=hub.advance_peer_session_epoch,
        clock=clock,
        reset_deadline_s=90.0,
    )

    await hub.replace_peer_stt_provider(peer_stt)
    await hub.start(auto_flush_osc=False)

    first_id = uuid4()
    await hub.handle_peer_vad_event(
        SpeechStart(first_id, pre_roll=samples(0.0), chunk=samples(1.0))
    )

    assert hub.peer_runtime.peer_epoch == 1
    assert len(backend.sessions) == 1

    await hub.handle_peer_vad_event(SpeechEnd(first_id))
    await asyncio.sleep(0)

    assert hub.peer_runtime.peer_epoch == 1
    assert len(backend.sessions) == 1

    await hub.peer_stt.close()
    second_id = uuid4()
    await hub.handle_peer_vad_event(
        SpeechStart(second_id, pre_roll=samples(0.0), chunk=samples(1.0))
    )

    assert hub.peer_runtime.peer_epoch == 2
    assert len(backend.sessions) == 2

    await hub.stop()


@pytest.mark.asyncio
async def test_peer_translation_respects_master_translation_toggle() -> None:
    llm = FakeLLM()
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        clock=FakeClock(_now=10.0),
        translation_enabled=False,
        peer_translation_enabled=True,
    )

    utterance_id = await hub.handle_peer_transcript_final_for_test(text="peer line")
    bundle = hub.get_or_create_bundle(utterance_id, channel="peer")
    event = await hub.ui_events.get()

    assert event.type == UIEventType.TRANSCRIPT_FINAL
    assert bundle.translation is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_peer_translation_requires_overlay_connected_runtime_gate() -> None:
    llm = FakeLLM()
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        clock=FakeClock(_now=10.0),
        peer_translation_enabled=True,
        overlay_connected=False,
    )

    utterance_id = await hub.handle_peer_transcript_final_for_test(text="peer line")
    bundle = hub.get_or_create_bundle(utterance_id, channel="peer")
    event = await hub.ui_events.get()

    assert event.type == UIEventType.TRANSCRIPT_FINAL
    assert bundle.translation is None
    assert llm.calls == []
