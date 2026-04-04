from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.orchestrator import hub as hub_module
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.vad.gating import SpeechEnd
from puripuly_heart.domain.events import STTFinalEvent, STTPartialEvent
from puripuly_heart.domain.models import Transcript
from tests.helpers.fakes import RecordingOscQueue


@dataclass(slots=True)
class RecordingOverlaySink:
    events: list[object] = field(default_factory=list)

    async def emit(self, event: object) -> None:
        self.events.append(event)


@dataclass(slots=True)
class FailingOverlaySink:
    async def emit(self, event: object) -> None:
        _ = event
        raise RuntimeError("overlay boom")


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


@dataclass(slots=True)
class ImmediateFailingStreamingLLMProvider(LLMProvider):
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
        raise self.error
        if False:
            yield ""

    async def close(self) -> None:
        return


@dataclass(slots=True)
class BlockingTranslateLLMProvider(LLMProvider):
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Future[None] | None = None

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ):
        _ = (utterance_id, text, system_prompt, source_language, target_language, context)
        self.started.set()
        if self.release is None:
            self.release = asyncio.get_running_loop().create_future()
        await self.release
        raise AssertionError("blocking provider should be cancelled before release")

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_hub_emits_self_and_peer_finals_to_overlay_sink() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), overlay_sink=sink)

    await hub.submit_text("self text", source="You")
    await hub.handle_peer_transcript_final_for_test(text="peer text")

    assert [event.type for event in sink.events] == [
        "self_transcript_final",
        "utterance_closed",
        "peer_transcript_final",
        "utterance_closed",
    ]
    assert [event.channel for event in sink.events] == ["self", "self", "peer", "peer"]


@pytest.mark.asyncio
async def test_chatbox_stays_self_final_only_while_overlay_sink_receives_peer_finals() -> None:
    osc = RecordingOscQueue()
    sink = RecordingOverlaySink()
    hub = ClientHub(stt=None, llm=None, osc=osc, overlay_sink=sink)

    await hub.submit_text("self text", source="You")
    await hub.handle_peer_transcript_final_for_test(text="peer text")

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
    final_events = [event for event in sink.events if event.type == "translation_final"]
    closed_events = [event for event in sink.events if event.type == "utterance_closed"]

    assert len(stream_events) == 1
    assert stream_events[0].text == "hello"
    assert final_events[-1].channel == "peer"
    assert final_events[-1].text == "hello"
    assert closed_events[-1].channel == "peer"
    assert closed_events[-1].is_final is True


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
async def test_hub_emits_self_translation_to_overlay_after_translation_completion() -> None:
    sink = RecordingOverlaySink()
    osc = RecordingOscQueue()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["hello"]),
        osc=osc,
        overlay_sink=sink,
    )

    await hub.submit_text("self text", source="You")
    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    translation_events = [
        event
        for event in sink.events
        if event.type == "translation_final" and event.channel == "self"
    ]

    assert osc.messages[0].text == "self text (hello)"
    assert [event.type for event in sink.events[:2]] == [
        "self_transcript_final",
        "translation_final",
    ]
    assert translation_events[-1].text == "hello"
    assert translation_events[-1].text != osc.messages[0].text


@pytest.mark.asyncio
async def test_hub_closes_self_overlay_line_after_translation_completion() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["hello"]),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
    )

    await hub.submit_text("self text", source="You")
    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    assert [event.type for event in sink.events] == [
        "self_transcript_final",
        "translation_final",
        "utterance_closed",
    ]
    assert sink.events[-1].channel == "self"
    assert sink.events[-1].is_final is True


@pytest.mark.asyncio
async def test_self_translation_failure_closes_overlay_line_as_incomplete() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=ImmediateFailingStreamingLLMProvider(error=RuntimeError("boom")),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
    )

    utterance_id = await hub.submit_text("self text", source="You")
    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    assert [event.type for event in sink.events] == [
        "self_transcript_final",
        "utterance_closed",
    ]
    assert sink.events[-1].channel == "self"
    assert sink.events[-1].utterance_id == utterance_id
    assert sink.events[-1].is_final is False


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


@pytest.mark.asyncio
async def test_peer_stream_failure_before_first_chunk_still_closes_line() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=ImmediateFailingStreamingLLMProvider(error=RuntimeError("boom")),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        peer_translation_enabled=True,
    )

    utterance_id = await hub.translate_peer_text_for_test("안녕")

    assert [event.type for event in sink.events] == [
        "peer_transcript_final",
        "utterance_closed",
    ]
    assert sink.events[-1].channel == "peer"
    assert sink.events[-1].utterance_id == utterance_id
    assert sink.events[-1].is_final is False


@pytest.mark.asyncio
async def test_self_translation_cancellation_closes_overlay_line_as_incomplete() -> None:
    sink = RecordingOverlaySink()
    llm = BlockingTranslateLLMProvider()
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
    )

    utterance_id = await hub.submit_text("self text", source="You")
    await llm.started.wait()
    await hub.self_runtime.reset_runtime_state()

    assert [event.type for event in sink.events] == [
        "self_transcript_final",
        "utterance_closed",
    ]
    assert sink.events[-1].channel == "self"
    assert sink.events[-1].utterance_id == utterance_id
    assert sink.events[-1].is_final is False


@pytest.mark.asyncio
async def test_low_latency_self_partial_no_longer_emits_overlay_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub_module, "_SELF_PREVIEW_COALESCE_MS", 10, raising=False)
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
    )
    utterance_id = uuid4()
    partial = Transcript(
        utterance_id=utterance_id, text="hello live", is_final=False, created_at=11.0
    )

    await hub._handle_stt_event(STTPartialEvent(utterance_id=utterance_id, transcript=partial))
    await asyncio.sleep(0.02)

    assert sink.events == []
    assert hub.ui_events.empty()


@pytest.mark.asyncio
async def test_low_latency_self_final_emits_active_update_with_merge_occupant_key() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )

    assert [event.type for event in sink.events] == ["self_active_update"]
    assert sink.events[0].text == "hello live"
    assert sink.events[0].occupant_key == f"self:{hub._merge_buffer.merge_id}"
    assert hub.ui_events.empty()


@pytest.mark.asyncio
async def test_low_latency_self_active_updates_only_when_merged_text_changes() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello",
                is_final=True,
                created_at=12.0,
            ),
        )
    )
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello",
                is_final=True,
                created_at=13.0,
            ),
        )
    )
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello world",
                is_final=True,
                created_at=14.0,
            ),
        )
    )

    assert [event.type for event in sink.events] == [
        "self_active_update",
        "self_active_update",
    ]
    assert [event.text for event in sink.events] == ["hello", "hello world"]


@pytest.mark.asyncio
async def test_low_latency_merge_commit_reuses_merge_identity_without_emitting_clear() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_finalize_wait_ms=0,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )
    active_event = sink.events[-1]
    await hub.handle_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=12.0,
            ),
        )
    )

    assert [event.type for event in sink.events] == [
        "self_active_update",
        "self_transcript_final",
        "utterance_closed",
    ]
    final_event = next(event for event in sink.events if event.type == "self_transcript_final")
    assert active_event.occupant_key == f"self:{final_event.utterance_id}"


@pytest.mark.asyncio
async def test_low_latency_self_active_update_failures_do_not_break_hub() -> None:
    sink = FailingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )

    assert hub.last_error_source == "overlay_sink"
    assert hub.ui_events.empty()
