from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.overlay.protocol import OverlayStateSnapshot
from tests.helpers.fakes import RecordingOscQueue


@dataclass(slots=True)
class RecordingOverlaySink:
    events: list[object] = field(default_factory=list)

    async def emit(self, event: object) -> None:
        self.events.append(event)

    def snapshot(self) -> OverlayStateSnapshot:
        return OverlayStateSnapshot(events=list(self.events))


@dataclass(slots=True)
class FailingOverlaySink:
    async def emit(self, event: object) -> None:
        _ = event
        raise RuntimeError("overlay boom")

    def snapshot(self) -> OverlayStateSnapshot:
        return OverlayStateSnapshot(events=[])


@dataclass(slots=True)
class StubStreamingLLMProvider(LLMProvider):
    chunks: list[str]

    async def stream_translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        _ = (utterance_id, text, system_prompt, source_language, target_language, context)
        for chunk in self.chunks:
            yield chunk

    async def close(self) -> None:
        return


@dataclass(slots=True)
class FailingAfterStreamingLLMProvider(LLMProvider):
    chunks: list[str]
    error: Exception

    async def stream_translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> AsyncIterator[str]:
        _ = (utterance_id, text, system_prompt, source_language, target_language, context)
        for chunk in self.chunks:
            yield chunk
        raise self.error

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_hub_emits_self_and_peer_finals_to_overlay_sink() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), overlay_sink=sink)

    await hub.submit_text("self text", source="You")
    await hub.handle_peer_transcript_final_for_test(text="peer text", speaker_label="Speaker 0")

    assert [event.type for event in sink.events] == [
        "self_transcript_final",
        "peer_transcript_final",
    ]
    assert [event.channel for event in sink.events] == ["self", "peer"]


@pytest.mark.asyncio
async def test_chatbox_stays_self_final_only_while_overlay_sink_receives_peer_finals() -> None:
    osc = RecordingOscQueue()
    sink = RecordingOverlaySink()
    hub = ClientHub(stt=None, llm=None, osc=osc, overlay_sink=sink)

    await hub.submit_text("self text", source="You")
    await hub.handle_peer_transcript_final_for_test(text="peer text", speaker_label="Speaker 0")

    assert len(osc.messages) == 1
    assert osc.messages[0].text == "self text"
    assert sink.events[-1].channel == "peer"


@pytest.mark.asyncio
async def test_peer_stream_updates_are_coalesced_before_overlay_emit() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["h", "he", "hello"]),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        peer_translation_enabled=True,
    )

    await hub.translate_peer_text_for_test("안녕")

    stream_events = [event for event in sink.events if event.type == "translation_stream_update"]

    assert len(stream_events) == 1
    assert stream_events[0].text == "hello"


@pytest.mark.asyncio
async def test_overlay_sink_failures_do_not_break_chatbox_or_translation_completion() -> None:
    sink = FailingOverlaySink()
    osc = RecordingOscQueue()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["hello"]),
        osc=osc,
        overlay_sink=sink,
    )

    await hub.submit_text("self text", source="You")
    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    assert osc.messages[0].text == "self text (hello)"
    assert hub.last_error_source == "overlay_sink"


@pytest.mark.asyncio
async def test_peer_stream_failure_keeps_latest_snapshot_and_closes_line_as_incomplete() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=FailingAfterStreamingLLMProvider(chunks=["h", "he"], error=RuntimeError("boom")),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        peer_translation_enabled=True,
    )

    await hub.translate_peer_text_for_test("안녕")

    stream_events = [event for event in sink.events if event.type == "translation_stream_update"]
    closed = [event for event in sink.events if event.type == "utterance_closed"][-1]

    assert stream_events[-1].text == "he"
    assert closed.is_final is False
