from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import numpy as np
import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub, ContextEntry, _MergeBuffer
from puripuly_heart.core.runtime_logging import SessionLoggingMode, SessionRuntimeLoggingService
from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd
from puripuly_heart.domain.events import (
    STTErrorEvent,
    STTFinalEvent,
    STTPartialEvent,
    STTSessionState,
    STTSessionStateEvent,
    UIEventType,
)
from puripuly_heart.domain.models import Transcript, Translation
from tests.helpers.fakes import RecordingOscQueue


@dataclass(slots=True)
class StubLLM:
    should_fail: bool = False
    calls: list[tuple[UUID, str, str]] = field(default_factory=list)
    closed: bool = False

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        _ = (system_prompt, source_language, target_language)
        self.calls.append((utterance_id, text, context))
        if self.should_fail:
            raise RuntimeError("llm failed")
        return Translation(utterance_id=utterance_id, text=f"T:{text}")

    async def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class StubSTT:
    handled: list[object] = field(default_factory=list)
    closed: bool = False

    async def handle_vad_event(self, event: object) -> None:
        self.handled.append(event)

    async def close(self) -> None:
        self.closed = True

    async def events(self):
        while True:
            await asyncio.sleep(60.0)
            yield STTBackendTranscriptEvent(text="", is_final=False)


@dataclass(slots=True)
class QueueingSTT:
    handled: list[object] = field(default_factory=list)
    closed: bool = False
    queue: asyncio.Queue[object | None] = field(default_factory=asyncio.Queue)

    async def handle_vad_event(self, event: object) -> None:
        self.handled.append(event)

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)

    async def emit(self, event: object) -> None:
        await self.queue.put(event)

    async def events(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


@dataclass(slots=True)
class BlockingOverlaySink:
    events: list[object] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def emit(self, event: object) -> None:
        self.events.append(event)
        self.started.set()
        await self.release.wait()


@dataclass(slots=True)
class RaisingOverlaySink:
    error: Exception = field(default_factory=lambda: RuntimeError("overlay down"))

    async def emit(self, event: object) -> None:
        _ = event
        raise self.error


@dataclass(slots=True)
class RaisingEventSTT:
    error: Exception = field(default_factory=lambda: RuntimeError("loop boom"))

    async def events(self):
        if False:
            yield None
        raise self.error


@dataclass(slots=True)
class _RuntimeLogSinks:
    stream_handler: logging.Handler
    file_handler: logging.Handler
    log_file: object


def _make_runtime_logging_capture() -> tuple[SessionRuntimeLoggingService, io.StringIO]:
    stream = io.StringIO()
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger(f"test.hub.runtime.root.{uuid4()}")
    root_logger.handlers.clear()
    root_logger.propagate = False

    session_logger = logging.getLogger(f"test.hub.runtime.session.{uuid4()}")
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


@pytest.mark.asyncio
async def test_hub_drops_stale_partial_and_keeps_final_order() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())
    buffer = _MergeBuffer(merge_id=uuid4())
    utterance_id = uuid4()

    hub._upsert_merge_part(buffer, utterance_id, "hello world")
    hub._upsert_merge_part(buffer, utterance_id, "hello")
    hub._upsert_merge_part(buffer, utterance_id, "hello world!!!")

    partial = Transcript(utterance_id=utterance_id, text="he", is_final=False, created_at=1.0)
    final = Transcript(
        utterance_id=utterance_id, text="hello world!!!", is_final=True, created_at=2.0
    )

    await hub._handle_transcript(partial, is_final=False, source="Mic")
    await hub._handle_transcript(final, is_final=True, source="Mic")
    await hub._handle_transcript(partial, is_final=False, source="Mic")

    bundle = hub.get_or_create_bundle(utterance_id)
    assert buffer.parts == ["hello world!!!"]
    assert hub._merge_text(buffer.parts) == "hello world!!!"
    assert bundle.final is not None
    assert bundle.final.text == "hello world!!!"
    assert bundle.partial is None


@pytest.mark.asyncio
async def test_stop_cancels_pending_tasks_and_closes_providers() -> None:
    stt = StubSTT()
    llm = StubLLM()
    hub = ClientHub(stt=stt, llm=llm, osc=RecordingOscQueue(), clock=FakeClock())
    hub._running = True

    hub._translation_tasks[uuid4()] = asyncio.create_task(asyncio.sleep(60.0))
    buffer = _MergeBuffer(merge_id=uuid4())
    buffer.spec_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.finalize_wait_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.awaiting_vad_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.resume_end_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    hub._merge_buffer = buffer

    await hub.stop()

    assert hub._translation_tasks == {}
    assert hub._merge_buffer is None
    assert stt.closed is True
    assert llm.closed is True


@pytest.mark.asyncio
async def test_start_is_idempotent_and_creates_background_tasks() -> None:
    stt = StubSTT()
    hub = ClientHub(stt=stt, llm=StubLLM(), osc=RecordingOscQueue(), clock=FakeClock())

    await hub.start(auto_flush_osc=True)
    stt_task = hub._stt_task
    osc_task = hub._osc_flush_task
    await hub.start(auto_flush_osc=True)

    assert hub._stt_task is stt_task
    assert hub._osc_flush_task is osc_task
    await hub.stop()


@pytest.mark.asyncio
async def test_replace_stt_provider_running_restarts_event_loop_and_clears_runtime_state() -> None:
    old_stt = QueueingSTT()
    new_stt = QueueingSTT()
    hub = ClientHub(stt=old_stt, llm=StubLLM(), osc=RecordingOscQueue(), clock=FakeClock())
    await hub.start(auto_flush_osc=False)
    old_task = hub._stt_task

    utterance_id = uuid4()
    hub.get_or_create_bundle(utterance_id)
    hub._utterance_sources[utterance_id] = "Mic"
    hub._utterance_start_times[utterance_id] = 1.0
    hub._speech_ended_ids.add(utterance_id)
    hub._translation_history.append(ContextEntry("hello", "ko", "en", 1.0))
    hub._translation_tasks[utterance_id] = asyncio.create_task(asyncio.sleep(60.0))
    hub._record_latency_stage(
        channel="self",
        utterance_id=utterance_id,
        stage="speech_end",
        timestamp=1.0,
        publish_now=False,
    )
    buffer = _MergeBuffer(merge_id=uuid4(), parts=["hello"], utterance_ids=[utterance_id])
    buffer.spec_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.finalize_wait_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.awaiting_vad_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.resume_end_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    hub._merge_buffer = buffer

    await hub.replace_stt_provider(new_stt)

    assert old_stt.closed is True
    assert hub.stt is new_stt
    assert hub._stt_task is not None
    assert hub._stt_task is not old_task
    assert hub._translation_tasks == {}
    assert hub._utterances == {}
    assert hub._utterance_sources == {}
    assert hub._utterance_start_times == {}
    assert hub._speech_ended_ids == set()
    assert hub._translation_history == []
    assert hub._merge_buffer is None
    assert hub._latency_timelines == {}

    await new_stt.emit(STTSessionStateEvent(state=STTSessionState.STREAMING))
    await asyncio.sleep(0)
    event = await hub.ui_events.get()
    assert event.type == UIEventType.SESSION_STATE_CHANGED

    await hub.stop()


@pytest.mark.asyncio
async def test_replace_stt_provider_none_stops_event_loop_and_clears_runtime_state() -> None:
    old_stt = QueueingSTT()
    hub = ClientHub(stt=old_stt, llm=StubLLM(), osc=RecordingOscQueue(), clock=FakeClock())
    await hub.start(auto_flush_osc=False)
    utterance_id = uuid4()
    hub._translation_history.append(ContextEntry("hello", "ko", "en", 1.0))
    hub._translation_tasks[utterance_id] = asyncio.create_task(asyncio.sleep(60.0))
    hub._record_latency_stage(
        channel="self",
        utterance_id=utterance_id,
        stage="speech_end",
        timestamp=1.0,
        publish_now=False,
    )

    await hub.replace_stt_provider(None)

    assert old_stt.closed is True
    assert hub.stt is None
    assert hub._stt_task is None
    assert hub._translation_tasks == {}
    assert hub._translation_history == []
    assert hub._latency_timelines == {}

    await hub.stop()


@pytest.mark.asyncio
async def test_replace_peer_stt_provider_running_restarts_event_loop_and_clears_runtime_state() -> (
    None
):
    old_stt = QueueingSTT()
    new_stt = QueueingSTT()
    hub = ClientHub(
        stt=None,
        peer_stt=old_stt,
        llm=StubLLM(),
        osc=RecordingOscQueue(),
        clock=FakeClock(),
    )
    await hub.start(auto_flush_osc=False)
    old_task = hub._peer_stt_task

    utterance_id = uuid4()
    hub.peer_runtime.get_or_create_bundle(utterance_id)
    hub.peer_runtime.utterance_sources[utterance_id] = "Peer"
    hub.peer_runtime.utterance_start_times[utterance_id] = 2.0
    hub.peer_runtime.speech_ended_ids.add(utterance_id)
    hub.peer_runtime.translation_history.append(
        ContextEntry("peer line", "en", "ko", 1.0, channel="peer")
    )
    hub.peer_runtime.translation_tasks[utterance_id] = asyncio.create_task(asyncio.sleep(60.0))
    hub._record_latency_stage(
        channel="peer",
        utterance_id=utterance_id,
        stage="speech_end",
        timestamp=2.0,
        publish_now=False,
    )
    buffer = _MergeBuffer(merge_id=uuid4(), parts=["peer"], utterance_ids=[utterance_id])
    buffer.spec_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.finalize_wait_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.awaiting_vad_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.resume_end_timeout_task = asyncio.create_task(asyncio.sleep(60.0))
    hub.peer_runtime.merge_buffer = buffer

    await hub.replace_peer_stt_provider(new_stt)

    assert old_stt.closed is True
    assert hub.peer_stt is new_stt
    assert hub.peer_runtime.stt is new_stt
    assert hub._peer_stt_task is not None
    assert hub._peer_stt_task is not old_task
    assert hub.peer_runtime.translation_tasks == {}
    assert hub.peer_runtime.utterances == {}
    assert hub.peer_runtime.utterance_sources == {}
    assert hub.peer_runtime.utterance_start_times == {}
    assert hub.peer_runtime.speech_ended_ids == set()
    assert hub.peer_runtime.translation_history == []
    assert hub.peer_runtime.merge_buffer is None
    assert hub._latency_timelines == {}

    await new_stt.emit(STTSessionStateEvent(state=STTSessionState.STREAMING, channel="peer"))
    await asyncio.sleep(0)
    event = await hub.ui_events.get()
    assert event.type == UIEventType.SESSION_STATE_CHANGED
    assert event.channel == "peer"

    await hub.stop()


def test_send_stt_connected_notification_respects_eligibility_and_interval() -> None:
    clock = FakeClock()
    osc = RecordingOscQueue(immediate_result=True)
    hub = ClientHub(stt=None, llm=None, osc=osc, clock=clock)

    hub._send_stt_connected_notification()
    assert osc.immediate_messages == []

    hub.mark_promo_eligible()
    hub._send_stt_connected_notification()
    assert osc.immediate_messages == ["PuriPuly ON!"]
    assert hub._last_promo_time == 0.0

    clock.advance(30.0)
    hub.mark_promo_eligible()
    hub._send_stt_connected_notification()
    assert osc.immediate_messages == ["PuriPuly ON!"]

    clock.advance(301.0)
    hub.mark_promo_eligible()
    hub._send_stt_connected_notification()
    assert osc.immediate_messages == ["PuriPuly ON!", "PuriPuly ON!"]


def test_send_stt_connected_notification_does_not_update_time_on_failed_send() -> None:
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(immediate_result=False),
        clock=FakeClock(),
    )

    hub.mark_promo_eligible()
    hub._send_stt_connected_notification()
    assert hub._last_promo_time is None


def test_prepare_llm_request_routes_context_logs_by_runtime_visibility() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    basic_hub = ClientHub(
        stt=None,
        llm=StubLLM(),
        osc=RecordingOscQueue(),
        clock=FakeClock(_now=10.0),
        runtime_logging=basic_runtime_logging,
    )
    detailed_hub = ClientHub(
        stt=None,
        llm=StubLLM(),
        osc=RecordingOscQueue(),
        clock=FakeClock(_now=10.0),
        runtime_logging=detailed_runtime_logging,
    )

    try:
        basic_hub._remember_context_entry("안녕", 9.0)
        detailed_hub._remember_context_entry("안녕", 9.0)

        basic_hub._prepare_llm_request_with_mode("입력")
        detailed_hub._prepare_llm_request_with_mode("입력")

        basic_messages = _runtime_log_messages(basic_stream)
        detailed_messages = _runtime_log_messages(detailed_stream)

        assert "[Hub] Context mode: channel=self mode=local" in basic_messages
        assert "[Hub] Context apply: channel=self text='입력' entries=1" in basic_messages
        assert '[Hub] Context[0]: [1s ago] "안녕"' in basic_messages

        assert "[Hub] Context mode: channel=self mode=local" in detailed_messages
        assert "[Hub] Context apply: channel=self text='입력' entries=1" in detailed_messages
        assert '[Hub] Context[0]: [1s ago] "안녕"' in detailed_messages
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()


@pytest.mark.asyncio
async def test_handle_stt_event_logs_basic_channel_state_breadcrumb() -> None:
    runtime_logging, log_stream = _make_runtime_logging_capture()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=runtime_logging,
    )

    try:
        await hub._handle_stt_event(
            STTSessionStateEvent(state=STTSessionState.STREAMING, channel="peer")
        )

        event = await hub.ui_events.get()
        assert event.type == UIEventType.SESSION_STATE_CHANGED
        assert event.channel == "peer"
        assert "[Hub] STT state: channel=peer state=STREAMING" in _runtime_log_messages(log_stream)
    finally:
        runtime_logging.close()


@pytest.mark.asyncio
async def test_enqueue_osc_emits_payload_preview_only_in_detailed_runtime_logs() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    basic_hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=basic_runtime_logging,
    )
    detailed_hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=detailed_runtime_logging,
    )
    utterance_id = uuid4()

    try:
        await basic_hub._enqueue_osc(
            utterance_id,
            transcript_text="hello world from transcript",
            translation_text="hello world translated",
        )
        await detailed_hub._enqueue_osc(
            utterance_id,
            transcript_text="hello world from transcript",
            translation_text="hello world translated",
        )

        basic_event = await basic_hub.ui_events.get()
        detailed_event = await detailed_hub.ui_events.get()
        assert basic_event.type == UIEventType.OSC_SENT
        assert detailed_event.type == UIEventType.OSC_SENT

        basic_messages = _runtime_log_messages(basic_stream)
        detailed_messages = _runtime_log_messages(detailed_stream)

        assert not any("OSC enqueue preview" in message for message in basic_messages)
        assert any(
            message.startswith("[Hub] OSC enqueue preview:")
            and "hello world from transcript (hello world translated)" in message
            for message in detailed_messages
        )
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()


@pytest.mark.asyncio
async def test_handle_stt_event_routes_non_low_latency_events() -> None:
    runtime_logging, log_stream = _make_runtime_logging_capture()
    runtime_logging.set_mode(SessionLoggingMode.DETAILED)
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=runtime_logging,
    )
    hub.mark_promo_eligible()
    utterance_id = uuid4()
    partial = Transcript(utterance_id=utterance_id, text="hel", is_final=False, created_at=1.0)
    final = Transcript(utterance_id=utterance_id, text="hello", is_final=True, created_at=2.0)

    try:
        await hub._handle_stt_event(STTSessionStateEvent(state=STTSessionState.STREAMING))
        await hub._handle_stt_event(STTErrorEvent(message="boom"))
        await hub._handle_stt_event(STTPartialEvent(utterance_id=utterance_id, transcript=partial))
        await hub._handle_stt_event(STTFinalEvent(utterance_id=utterance_id, transcript=final))

        events = [await hub.ui_events.get() for _ in range(5)]
        assert [event.type for event in events] == [
            UIEventType.SESSION_STATE_CHANGED,
            UIEventType.ERROR,
            UIEventType.TRANSCRIPT_PARTIAL,
            UIEventType.TRANSCRIPT_FINAL,
            UIEventType.OSC_SENT,
        ]
        assert events[1].runtime_log_handled is False
        assert hub.osc.immediate_messages == ["PuriPuly ON!"]
        assert len(hub.osc.messages) == 1
        assert hub.osc.messages[0].text == "hello"
        assert (
            "[Hub] Translation skipped (stage=final, channel=self, publish_chatbox=True): "
            "llm unavailable"
        ) in _runtime_log_messages(log_stream)
    finally:
        runtime_logging.close()


@pytest.mark.asyncio
async def test_handle_stt_event_ignores_partial_in_low_latency_mode() -> None:
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        low_latency_mode=True,
    )
    utterance_id = uuid4()
    partial = Transcript(utterance_id=utterance_id, text="hel", is_final=False, created_at=1.0)

    await hub._handle_stt_event(STTPartialEvent(utterance_id=utterance_id, transcript=partial))

    assert hub.ui_events.empty()


@pytest.mark.asyncio
async def test_translate_and_enqueue_emits_error_and_fallback_transcript() -> None:
    llm = StubLLM(should_fail=True)
    runtime_logging, log_stream = _make_runtime_logging_capture()
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        fallback_transcript_only=True,
        runtime_logging=runtime_logging,
    )
    utterance_id = uuid4()

    try:
        await hub._translate_and_enqueue(utterance_id, "hello")

        events = [await hub.ui_events.get() for _ in range(2)]
        assert [event.type for event in events] == [UIEventType.ERROR, UIEventType.OSC_SENT]
        assert events[0].runtime_log_handled is True
        assert hub.osc.messages[0].text == "hello"
        assert (
            "[Hub] Translation failed (stage=final, channel=self): llm failed"
            in _runtime_log_messages(log_stream)
        )
    finally:
        runtime_logging.close()


@pytest.mark.asyncio
async def test_try_commit_after_spec_respects_allow_fallback_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = ClientHub(stt=None, llm=StubLLM(), osc=RecordingOscQueue(), clock=FakeClock())
    buffer = _MergeBuffer(merge_id=uuid4(), parts=["text"])
    hub._merge_buffer = buffer
    called: list[str] = []

    async def fake_commit(_self: ClientHub, _buffer: _MergeBuffer, *, reason: str) -> None:
        called.append(reason)

    monkeypatch.setattr(ClientHub, "_commit_merge", fake_commit)

    await hub._try_commit_after_spec(buffer, reason="spec_failed", allow_fallback=False)
    await hub._try_commit_after_spec(buffer, reason="spec_failed", allow_fallback=True)

    assert called == ["spec_failed"]


@pytest.mark.asyncio
async def test_run_spec_translation_logs_spec_failure_only_in_detailed_mode() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    basic_hub = ClientHub(
        stt=None,
        llm=StubLLM(should_fail=True),
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=basic_runtime_logging,
        low_latency_mode=True,
    )
    detailed_hub = ClientHub(
        stt=None,
        llm=StubLLM(should_fail=True),
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        runtime_logging=detailed_runtime_logging,
        low_latency_mode=True,
    )
    basic_buffer = _MergeBuffer(
        merge_id=uuid4(), parts=["hello"], spec_text="hello", spec_attempts=1
    )
    detailed_buffer = _MergeBuffer(
        merge_id=uuid4(), parts=["hello"], spec_text="hello", spec_attempts=1
    )
    basic_hub._merge_buffer = basic_buffer
    detailed_hub._merge_buffer = detailed_buffer

    try:
        await basic_hub._run_spec_translation(basic_buffer.merge_id, "hello", 1)
        await detailed_hub._run_spec_translation(detailed_buffer.merge_id, "hello", 1)

        assert not any(
            "[Hub] Translation failed (stage=spec, channel=self): llm failed" in message
            for message in _runtime_log_messages(basic_stream)
        )
        assert any(
            "[Hub] Translation failed (stage=spec, channel=self): llm failed" in message
            for message in _runtime_log_messages(detailed_stream)
        )
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()


@pytest.mark.asyncio
async def test_handle_stt_event_preserves_runtime_logged_flag_from_stt_errors() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())

    await hub._handle_stt_event(
        STTErrorEvent(message="session failed", channel="peer", runtime_log_handled=True)
    )

    event = await hub.ui_events.get()
    assert event.type == UIEventType.ERROR
    assert event.channel == "peer"
    assert event.runtime_log_handled is True


@pytest.mark.asyncio
async def test_run_stt_event_loop_without_runtime_logging_preserves_traceback(caplog) -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())

    with (
        pytest.raises(RuntimeError, match="loop boom"),
        caplog.at_level(logging.ERROR, logger="puripuly_heart.core.orchestrator.hub"),
    ):
        await hub._run_stt_event_loop(RaisingEventSTT())

    assert "[Hub] STT event loop crashed: loop boom" in caplog.messages
    assert any(record.exc_info is not None for record in caplog.records)


@pytest.mark.asyncio
async def test_emit_overlay_event_routes_traceback_to_detailed_runtime_logs() -> None:
    basic_runtime_logging, basic_stream = _make_runtime_logging_capture()
    detailed_runtime_logging, detailed_stream = _make_runtime_logging_capture()
    detailed_runtime_logging.set_mode(SessionLoggingMode.DETAILED)

    basic_hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=RaisingOverlaySink(),
        clock=FakeClock(),
        runtime_logging=basic_runtime_logging,
    )
    detailed_hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=RaisingOverlaySink(),
        clock=FakeClock(),
        runtime_logging=detailed_runtime_logging,
    )

    try:
        await basic_hub._emit_overlay_event(object())
        await detailed_hub._emit_overlay_event(object())

        basic_messages = _runtime_log_messages(basic_stream)
        detailed_messages = _runtime_log_messages(detailed_stream)

        assert "[Hub] Overlay sink emit failed: overlay down" in basic_messages
        assert not any(
            "Traceback (most recent call last):" in message for message in basic_messages
        )

        assert "[Hub] Overlay sink emit failed: overlay down" in detailed_messages
        assert any("Traceback (most recent call last):" in message for message in detailed_messages)
        assert any("RuntimeError: overlay down" in message for message in detailed_messages)
    finally:
        basic_runtime_logging.close()
        detailed_runtime_logging.close()


@pytest.mark.asyncio
async def test_maybe_restart_spec_replaces_previous_task_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = ClientHub(stt=None, llm=StubLLM(), osc=RecordingOscQueue(), clock=FakeClock())
    buffer = _MergeBuffer(merge_id=uuid4(), parts=["final text"])
    hub._merge_buffer = buffer
    old_task = asyncio.create_task(asyncio.sleep(60.0))
    buffer.spec_task = old_task
    buffer.spec_text = "old"
    buffer.spec_translation = Translation(utterance_id=buffer.merge_id, text="old")
    seen: list[tuple[UUID, str, int]] = []

    async def fake_run_spec(_self: ClientHub, merge_id: UUID, text: str, attempt: int) -> None:
        seen.append((merge_id, text, attempt))

    monkeypatch.setattr(ClientHub, "_run_spec_translation", fake_run_spec)
    await hub._maybe_restart_spec(buffer)
    await asyncio.sleep(0)

    assert old_task.done() is True
    assert buffer.spec_attempts == 1
    assert buffer.spec_text == "final text"
    assert seen == [(buffer.merge_id, "final text", 1)]


@pytest.mark.asyncio
async def test_handle_vad_event_speech_end_tracks_timing_and_forwards_to_stt() -> None:
    stt = StubSTT()
    clock = FakeClock(_now=10.0)
    hub = ClientHub(stt=stt, llm=None, osc=RecordingOscQueue(), clock=clock, low_latency_mode=True)
    utterance_id = uuid4()

    await hub.handle_vad_event(SpeechEnd(utterance_id))

    assert hub.osc.typing == [True]
    assert hub._utterance_start_times[utterance_id] == 10.0
    assert utterance_id in hub._speech_ended_ids
    assert stt.handled == [SpeechEnd(utterance_id)]


@pytest.mark.asyncio
async def test_handle_vad_event_forwards_resume_confirming_chunk_before_overlay_resync() -> None:
    stt = StubSTT()
    sink = BlockingOverlaySink()
    clock = FakeClock(_now=10.0)
    hub = ClientHub(
        stt=stt,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=clock,
        low_latency_mode=True,
    )
    first_utterance_id = uuid4()
    resumed_utterance_id = uuid4()
    merge_id = uuid4()
    chunk = SpeechChunk(resumed_utterance_id, chunk=np.zeros((1,), dtype=np.float32))

    hub._merge_buffer = _MergeBuffer(
        merge_id=merge_id,
        parts=["hello live"],
        utterance_ids=[first_utterance_id],
        spec_text="hello live",
        spec_translation=Translation(utterance_id=merge_id, text="translated live"),
        resume_pending=True,
        resume_utterance_id=resumed_utterance_id,
        resume_chunk_count=2,
    )
    hub._overlay_active_self_text = "stale preview"
    hub._overlay_active_self_secondary_text = "translated live"

    task = asyncio.create_task(hub.handle_vad_event(chunk))
    await sink.started.wait()

    assert len(stt.handled) == 1
    assert stt.handled[0] is chunk
    assert task.done() is False

    sink.release.set()
    await task

    assert sink.events[-1].type == "self_active_update"
    assert sink.events[-1].text == "hello live"
    assert sink.events[-1].secondary_text == "translated live"


@pytest.mark.asyncio
async def test_submit_text_validates_input_and_enqueues_without_llm() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())

    with pytest.raises(ValueError, match="text must be non-empty"):
        await hub.submit_text("   ")

    utterance_id = await hub.submit_text("hello", source="You")
    events = [await hub.ui_events.get(), await hub.ui_events.get()]
    assert [event.type for event in events] == [UIEventType.TRANSCRIPT_FINAL, UIEventType.OSC_SENT]
    assert hub.osc.messages[-1].utterance_id == utterance_id
    assert hub.osc.messages[-1].text == "hello"


def test_merge_helpers_cover_overlap_and_spacing_paths() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())

    assert hub._merge_with_overlap("same text", "text done") == "same text done"
    assert hub._merge_with_overlap("go", "home") == "go home"
    assert hub._merge_with_overlap("abc", "...abc") == "abc"
    assert hub._merge_with_overlap("가다.", "가다고") == "가다.가다고"
    assert hub._strip_trailing_boundary("abc. ") == ("abc", 2)
    assert hub._strip_leading_boundary(" ..abc") == ("abc", 3)
