from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.orchestrator.hub import ClientHub, _MergeBuffer
from puripuly_heart.core.stt.backend import STTBackendTranscriptEvent
from puripuly_heart.core.vad.gating import SpeechEnd
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


@pytest.mark.asyncio
async def test_handle_stt_event_routes_non_low_latency_events() -> None:
    hub = ClientHub(stt=None, llm=None, osc=RecordingOscQueue(), clock=FakeClock())
    hub.mark_promo_eligible()
    utterance_id = uuid4()
    partial = Transcript(utterance_id=utterance_id, text="hel", is_final=False, created_at=1.0)
    final = Transcript(utterance_id=utterance_id, text="hello", is_final=True, created_at=2.0)

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
    assert hub.osc.immediate_messages == ["PuriPuly ON!"]
    assert len(hub.osc.messages) == 1
    assert hub.osc.messages[0].text == "hello"


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
    hub = ClientHub(
        stt=None,
        llm=llm,
        osc=RecordingOscQueue(),
        clock=FakeClock(),
        fallback_transcript_only=True,
    )
    utterance_id = uuid4()

    await hub._translate_and_enqueue(utterance_id, "hello")

    events = [await hub.ui_events.get() for _ in range(2)]
    assert [event.type for event in events] == [UIEventType.ERROR, UIEventType.OSC_SENT]
    assert hub.osc.messages[0].text == "hello"


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
