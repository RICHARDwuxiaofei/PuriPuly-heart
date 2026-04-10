from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import numpy as np
import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.orchestrator import hub as hub_module
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.overlay.diagnostics import OverlayDiagnosticsRecorder
from puripuly_heart.core.overlay.presenter import OverlayPresenter
from puripuly_heart.core.runtime_logging import (
    LATENCY_TRACE_POINT_CONTRACTS,
    SessionLoggingMode,
)
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.events import STTFinalEvent, STTPartialEvent
from puripuly_heart.domain.models import Transcript
from puripuly_heart.ui.overlay_calibration import OverlayCalibration
from tests.core.test_hub_branch_coverage import (
    _make_runtime_logging_capture,
    _runtime_log_messages,
)
from tests.helpers.fakes import RecordingOscQueue


@dataclass(slots=True)
class RecordingOverlaySink:
    events: list[object] = field(default_factory=list)

    async def emit(self, event: object) -> None:
        self.events.append(event)


@dataclass(slots=True)
class RecordingPresentationBridge:
    snapshots: list[object] = field(default_factory=list)

    async def replace_snapshot(self, snapshot: object) -> None:
        self.snapshots.append(snapshot)

    async def broadcast_shutdown(self) -> None:
        return


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


@dataclass(slots=True)
class ReleasableTranslateLLMProvider(LLMProvider):
    response_text: str
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
        return hub_module.Translation(utterance_id=utterance_id, text=self.response_text)

    async def close(self) -> None:
        return


@dataclass(slots=True)
class ClockedStreamingLLMProvider(LLMProvider):
    clock: FakeClock
    snapshots: list[tuple[float, str]]
    final_pause_s: float = 0.0

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
        for delay_s, snapshot in self.snapshots:
            self.clock.advance(delay_s)
            yield snapshot
        self.clock.advance(self.final_pause_s)

    async def close(self) -> None:
        return


@dataclass(slots=True)
class SequencedTranslateLLMProvider(LLMProvider):
    responses: list[str]
    delay_s: float = 0.01
    calls: list[str] = field(default_factory=list)

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
        _ = (utterance_id, system_prompt, source_language, target_language, context)
        self.calls.append(text)
        await asyncio.sleep(self.delay_s)
        if not self.responses:
            raise AssertionError("no translate response configured")
        return hub_module.Translation(utterance_id=utterance_id, text=self.responses.pop(0))

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
async def test_peer_overlay_first_emit_latency_summary_and_detailed_trace() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    basic_clock = FakeClock(_now=10.0)
    detailed_clock = FakeClock(_now=20.0)
    basic_hub = ClientHub(
        stt=None,
        llm=ClockedStreamingLLMProvider(
            clock=basic_clock,
            snapshots=[(0.04, "he"), (0.06, "hello")],
            final_pause_s=0.05,
        ),
        osc=RecordingOscQueue(),
        overlay_sink=RecordingOverlaySink(),
        peer_translation_enabled=True,
        runtime_logging=basic_runtime_logging,
        clock=basic_clock,
    )
    detailed_hub = ClientHub(
        stt=None,
        llm=ClockedStreamingLLMProvider(
            clock=detailed_clock,
            snapshots=[(0.04, "he"), (0.06, "hello")],
            final_pause_s=0.05,
        ),
        osc=RecordingOscQueue(),
        overlay_sink=RecordingOverlaySink(),
        peer_translation_enabled=True,
        runtime_logging=detailed_runtime_logging,
        clock=detailed_clock,
    )

    try:
        basic_utterance_id = uuid4()
        await basic_hub.handle_peer_vad_event(SpeechEnd(basic_utterance_id))
        basic_clock.advance(0.03)
        await basic_hub._handle_stt_event(
            STTFinalEvent(
                utterance_id=basic_utterance_id,
                transcript=Transcript(
                    utterance_id=basic_utterance_id,
                    text="안녕",
                    is_final=True,
                    created_at=basic_clock.now(),
                    channel="peer",
                ),
            )
        )
        await asyncio.gather(
            *basic_hub.peer_runtime.translation_tasks.values(), return_exceptions=True
        )

        detailed_utterance_id = uuid4()
        await detailed_hub.handle_peer_vad_event(SpeechEnd(detailed_utterance_id))
        detailed_clock.advance(0.03)
        await detailed_hub._handle_stt_event(
            STTFinalEvent(
                utterance_id=detailed_utterance_id,
                transcript=Transcript(
                    utterance_id=detailed_utterance_id,
                    text="안녕",
                    is_final=True,
                    created_at=detailed_clock.now(),
                    channel="peer",
                ),
            )
        )
        await asyncio.gather(
            *detailed_hub.peer_runtime.translation_tasks.values(), return_exceptions=True
        )

        basic_messages = _runtime_log_messages(basic_stream)
        detailed_messages = _runtime_log_messages(detailed_stream)
        basic_latency_message = next(
            message for message in basic_messages if "[Basic][Latency]" in message
        )

        assert "channel=peer" in basic_latency_message
        assert "e2e_ms=180" in basic_latency_message
        assert "final_output_stage=peer_overlay_first_emit" in basic_latency_message
        assert not any("[Detailed][Latency]" in message for message in basic_messages)
        assert not any("[Detailed][LatencyBreakdown]" in message for message in basic_messages)

        for stage in (
            "speech_end",
            "stt_final",
            "llm_request_start",
            "llm_first_chunk",
            "llm_done",
            "peer_overlay_first_emit",
        ):
            assert any(
                "[Detailed][Latency]" in message and f"stage={stage}" in message
                for message in detailed_messages
            )
        assert any(
            "[Detailed][LatencyBreakdown]" in message
            and "channel=peer" in message
            and "speech_end_to_stt_final_ms=30" in message
            and "stt_final_to_final_output_ms=150" in message
            and "final_output_stage=peer_overlay_first_emit" in message
            for message in detailed_messages
        )
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()
        await basic_hub.stop()
        await detailed_hub.stop()


@pytest.mark.asyncio
async def test_peer_detailed_latency_trace_survives_basic_to_detailed_mode_switch() -> None:
    runtime_logging, log_stream = _make_runtime_logging_capture()
    clock = FakeClock(_now=10.0)
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        runtime_logging=runtime_logging,
        clock=clock,
    )
    utterance_id = uuid4()

    try:
        await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
        runtime_logging.set_mode(SessionLoggingMode.DETAILED)
        clock.advance(0.05)

        await hub._handle_stt_event(
            STTFinalEvent(
                utterance_id=utterance_id,
                transcript=Transcript(
                    utterance_id=utterance_id,
                    text="안녕",
                    is_final=True,
                    created_at=clock.now(),
                    channel="peer",
                ),
            )
        )

        messages = _runtime_log_messages(log_stream)
        assert any(
            "[Detailed][Latency]" in message and "stage=speech_end" in message
            for message in messages
        )
        assert any(
            "[Detailed][Latency]" in message and "stage=stt_final" in message
            for message in messages
        )
    finally:
        runtime_logging.close()
        await hub.stop()


@pytest.mark.asyncio
async def test_peer_overlay_success_clears_latency_timeline() -> None:
    utterance_id = uuid4()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["h", "he", "hello"]),
        osc=RecordingOscQueue(),
        overlay_sink=RecordingOverlaySink(),
        peer_translation_enabled=True,
        clock=FakeClock(_now=10.0),
    )

    await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="안녕",
                is_final=True,
                created_at=hub.clock.now(),
                channel="peer",
            ),
        )
    )
    await asyncio.gather(*hub.peer_runtime.translation_tasks.values(), return_exceptions=True)

    assert hub._latency_timelines == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()


@pytest.mark.asyncio
async def test_peer_overlay_translation_defers_bookkeeping_cleanup_until_chatbox_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    utterance_id = uuid4()
    hub = ClientHub(
        stt=None,
        llm=StubStreamingLLMProvider(chunks=["h", "he", "hello"]),
        osc=RecordingOscQueue(),
        overlay_sink=RecordingOverlaySink(),
        peer_translation_enabled=True,
        clock=FakeClock(_now=10.0),
    )
    hub.active_chatbox_channel = "peer"
    saw_live_peer_state = False

    async def fake_enqueue(
        self, enqueue_utterance_id, *, transcript_text: str, translation_text: str | None
    ):
        nonlocal saw_live_peer_state
        _ = (self, transcript_text, translation_text)
        assert enqueue_utterance_id == utterance_id
        assert enqueue_utterance_id in hub.peer_runtime.utterance_start_times
        assert enqueue_utterance_id in hub.peer_runtime.speech_ended_ids
        saw_live_peer_state = True
        hub.peer_runtime.utterance_start_times.pop(enqueue_utterance_id, None)
        hub.peer_runtime.speech_ended_ids.discard(enqueue_utterance_id)
        hub._finalize_latency_timeline(channel="peer", utterance_id=enqueue_utterance_id)

    monkeypatch.setattr(ClientHub, "_enqueue_osc", fake_enqueue)

    await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="안녕",
                is_final=True,
                created_at=hub.clock.now(),
                channel="peer",
            ),
        )
    )
    await asyncio.gather(*hub.peer_runtime.translation_tasks.values(), return_exceptions=True)

    assert saw_live_peer_state is True
    assert hub._latency_timelines == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()


@pytest.mark.asyncio
async def test_peer_overlay_failure_clears_latency_timeline() -> None:
    utterance_id = uuid4()
    hub = ClientHub(
        stt=None,
        llm=FailingAfterStreamingLLMProvider(chunks=["h", "he"], error=RuntimeError("boom")),
        osc=RecordingOscQueue(),
        overlay_sink=RecordingOverlaySink(),
        peer_translation_enabled=True,
        clock=FakeClock(_now=10.0),
    )

    await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="안녕",
                is_final=True,
                created_at=hub.clock.now(),
                channel="peer",
            ),
        )
    )
    await asyncio.gather(*hub.peer_runtime.translation_tasks.values(), return_exceptions=True)

    assert hub._latency_timelines == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()


@pytest.mark.asyncio
async def test_peer_no_chatbox_terminal_path_clears_latency_bookkeeping() -> None:
    utterance_id = uuid4()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(_now=10.0),
    )

    await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="안녕",
                is_final=True,
                created_at=hub.clock.now(),
                channel="peer",
            ),
        )
    )

    assert hub._latency_timelines == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()


@pytest.mark.asyncio
async def test_peer_no_overlay_translation_path_keeps_latency_bookkeeping_until_translation_finishes() -> (
    None
):
    utterance_id = uuid4()
    llm = ReleasableTranslateLLMProvider(response_text="hello")
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        peer_translation_enabled=True,
        clock=FakeClock(_now=10.0),
    )

    await hub.handle_peer_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="안녕",
                is_final=True,
                created_at=hub.clock.now(),
                channel="peer",
            ),
        )
    )
    await llm.started.wait()

    assert utterance_id in hub.peer_runtime.utterance_start_times
    assert utterance_id in hub.peer_runtime.speech_ended_ids
    assert ("peer", utterance_id) in hub._latency_timelines

    assert llm.release is not None
    llm.release.set_result(None)
    await asyncio.gather(*hub.peer_runtime.translation_tasks.values(), return_exceptions=True)

    assert hub._latency_timelines == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()


def test_peer_overlay_first_render_latency_contract_is_explicit() -> None:
    first_emit = LATENCY_TRACE_POINT_CONTRACTS["peer_overlay_first_emit"]
    first_render = LATENCY_TRACE_POINT_CONTRACTS["peer_overlay_first_render"]

    assert "final peer overlay output" in first_emit.timing_semantics
    assert "overlay_sink.emit" in first_emit.acceptance_expectation
    assert "downstream overlay" in first_render.timing_semantics
    assert "after peer_overlay_first_emit" in first_render.acceptance_expectation
    assert "once per utterance" in first_render.acceptance_expectation


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
async def test_hub_keeps_recently_translated_self_row_visible_until_newer_translation_arrives() -> (
    None
):
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        visible_window_target_blocks=1,
    )
    hub = ClientHub(
        stt=None,
        llm=SequencedTranslateLLMProvider(
            responses=["translated first", "translated second"],
            delay_s=0.05,
        ),
        osc=RecordingOscQueue(),
        overlay_sink=presenter,
        clock=clock,
    )

    first_id = await hub.submit_text("first", source="You")
    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{first_id}"]
    assert presenter.snapshot().blocks[0].secondary_text == "translated first"

    second_id = await hub.submit_text("second", source="You")

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{first_id}"]
    assert presenter.snapshot().blocks[0].secondary_text == "translated first"

    await asyncio.gather(*hub.self_runtime.translation_tasks.values(), return_exceptions=True)

    assert [block.id for block in presenter.snapshot().blocks] == [f"self:{second_id}"]
    assert presenter.snapshot().blocks[0].secondary_text == "translated second"


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
async def test_low_latency_self_spec_translation_re_emits_active_update_with_secondary_only() -> (
    None
):
    sink = RecordingOverlaySink()
    osc = RecordingOscQueue()
    hub = ClientHub(
        stt=None,
        llm=SequencedTranslateLLMProvider(responses=["translated live"]),
        osc=osc,
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_awaiting_vad_timeout_s=10.0,
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
    buffer = hub._merge_buffer
    assert buffer is not None
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    assert [event.type for event in sink.events] == [
        "self_active_update",
        "self_active_update",
    ]
    assert sink.events[0].text == "hello live"
    assert sink.events[0].secondary_text == ""
    assert sink.events[1].occupant_key == sink.events[0].occupant_key
    assert sink.events[1].text == "hello live"
    assert sink.events[1].secondary_text == "translated live"
    assert [event.type for event in sink.events if event.type != "self_active_update"] == []
    assert hub._merge_buffer is buffer
    assert osc.messages == []


@pytest.mark.asyncio
async def test_low_latency_self_active_secondary_stays_sticky_on_soft_reuse_mismatch_then_recovers() -> (
    None
):
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=SequencedTranslateLLMProvider(responses=["translated one", "translated two"]),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_awaiting_vad_timeout_s=10.0,
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
    buffer = hub._merge_buffer
    assert buffer is not None
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="bye now",
                is_final=True,
                created_at=12.0,
            ),
        )
    )
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    active_events = [event for event in sink.events if event.type == "self_active_update"]
    assert [event.secondary_text for event in active_events] == [
        "",
        "translated one",
        "translated one",
        "translated two",
    ]
    assert [event.text for event in active_events] == [
        "hello live",
        "hello live",
        "hello live bye now",
        "hello live bye now",
    ]
    assert [event.type for event in sink.events if event.type != "self_active_update"] == []


@pytest.mark.asyncio
async def test_low_latency_self_active_secondary_diagnostics_record_blank_sticky_and_spec_sources(
    tmp_path,
) -> None:
    sink = RecordingOverlaySink()
    diagnostics = OverlayDiagnosticsRecorder(
        overlay_instance_id="overlay-test",
        diagnostics_dir=tmp_path,
    )
    hub = ClientHub(
        stt=None,
        llm=SequencedTranslateLLMProvider(responses=["translated one", "translated two"]),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        overlay_diagnostics=diagnostics,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_awaiting_vad_timeout_s=10.0,
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
    buffer = hub._merge_buffer
    assert buffer is not None
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="bye now",
                is_final=True,
                created_at=12.0,
            ),
        )
    )

    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)
    assert list(diagnostics.hub_events) == []


@pytest.mark.asyncio
async def test_low_latency_self_active_secondary_stays_sticky_through_resume_continuation() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=SequencedTranslateLLMProvider(responses=["translated live", "translated continued"]),
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_awaiting_vad_timeout_s=10.0,
    )
    first_utterance_id = uuid4()
    resumed_utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=first_utterance_id,
            transcript=Transcript(
                utterance_id=first_utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )
    buffer = hub._merge_buffer
    assert buffer is not None
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    await hub.handle_vad_event(
        SpeechStart(
            resumed_utterance_id,
            pre_roll=np.zeros((0,), dtype=np.float32),
            chunk=np.zeros((1,), dtype=np.float32),
        )
    )
    for _ in range(3):
        await hub.handle_vad_event(
            SpeechChunk(
                resumed_utterance_id,
                chunk=np.zeros((1,), dtype=np.float32),
            )
        )

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=resumed_utterance_id,
            transcript=Transcript(
                utterance_id=resumed_utterance_id,
                text="again",
                is_final=True,
                created_at=12.0,
            ),
        )
    )
    assert buffer.spec_task is not None
    await asyncio.gather(buffer.spec_task, return_exceptions=True)

    active_events = [event for event in sink.events if event.type == "self_active_update"]
    assert [event.secondary_text for event in active_events] == [
        "",
        "translated live",
        "translated live",
        "translated continued",
    ]
    assert [event.text for event in active_events] == [
        "hello live",
        "hello live",
        "hello live again",
        "hello live again",
    ]
    assert [event.type for event in sink.events if event.type != "self_active_update"] == []
    assert hub._merge_buffer is buffer


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
