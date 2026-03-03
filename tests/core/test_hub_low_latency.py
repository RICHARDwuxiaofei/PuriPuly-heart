"""Unit tests for low-latency mode awaiting_vad_end bug fix."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import numpy as np
import pytest

from puripuly_heart.core.orchestrator.hub import ClientHub, _MergeBuffer
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.models import Transcript, Translation

# ── Mock classes ──────────────────────────────────────────────────────────────


class FakeClock:
    """Fake clock for testing time-based logic."""

    def __init__(self, initial_time: float = 0.0):
        self._time = initial_time

    def now(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


@dataclass
class FakeLLMProvider:
    """Fake LLM provider that records calls."""

    calls: list[dict] = field(default_factory=list)
    response_text: str = "translated"
    delay_s: float = 0.01

    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ):
        self.calls.append(
            {
                "utterance_id": utterance_id,
                "text": text,
                "context": context,
            }
        )
        await asyncio.sleep(self.delay_s)
        return Translation(utterance_id=utterance_id, text=self.response_text)

    async def close(self) -> None:
        pass


@dataclass
class FakeOscQueue:
    """Fake OSC queue that records enqueued messages."""

    messages: list = field(default_factory=list)

    def enqueue(self, msg) -> None:
        self.messages.append(msg)

    def send_typing(self, on: bool) -> None:
        pass

    def send_immediate(self, text: str) -> bool:
        return True

    def process_due(self) -> None:
        pass


def samples(value: float, n: int = 512) -> np.ndarray:
    return np.full((n,), value, dtype=np.float32)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSpeechEndedTracking:
    """Test _speech_ended_ids tracking."""

    @pytest.mark.asyncio
    async def test_speech_end_before_stt_final_uses_post_end_phase(self):
        """SpeechEnd가 먼저 오면 phase=post_end로 처리되어야 함."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
        )

        uid = uuid4()

        # 1. SpeechStart
        await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))

        # 2. SpeechChunk (3개)
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid, chunk=samples(0.5)))

        # 3. SpeechEnd 먼저 도착
        await hub.handle_vad_event(SpeechEnd(uid))

        # 4. _speech_ended_ids에 추가되었는지 확인
        assert uid in hub._speech_ended_ids

        # 5. STT Final 이벤트 직접 호출
        transcript = Transcript(
            utterance_id=uid, text="테스트", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        # 6. awaiting_vad_end=False 확인 (post_end로 처리됨)
        buffer = hub._merge_buffer
        assert buffer is not None
        assert buffer.awaiting_vad_end is False

        await hub.stop()

    @pytest.mark.asyncio
    async def test_stt_final_before_speech_end_waits_for_vad_end(self):
        """STT Final이 먼저 오면 awaiting_vad_end=True가 되어야 함."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_awaiting_vad_timeout_s=10.0,  # 긴 타임아웃
        )

        uid = uuid4()

        # SpeechEnd 없이 STT Final 직접 전송
        transcript = Transcript(
            utterance_id=uid, text="테스트", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        # awaiting_vad_end=True 확인
        buffer = hub._merge_buffer
        assert buffer is not None
        assert buffer.awaiting_vad_end is True
        assert buffer.awaiting_vad_utterance_id == uid

        # SpeechEnd 전송
        await hub.handle_vad_event(SpeechEnd(uid))

        # awaiting_vad_end=False로 클리어됨
        assert buffer.awaiting_vad_end is False

        await hub.stop()

    @pytest.mark.asyncio
    async def test_speech_ended_ids_cleaned_on_commit(self):
        """커밋 시 _speech_ended_ids가 정리되어야 함."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=None,  # LLM 없이 직접 커밋
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=0,  # 즉시 커밋
        )

        uid = uuid4()

        # SpeechEnd 도착
        await hub.handle_vad_event(SpeechEnd(uid))
        assert uid in hub._speech_ended_ids

        # STT Final 전송 (LLM 없으므로 바로 커밋)
        transcript = Transcript(
            utterance_id=uid, text="테스트", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        # 커밋 후 정리됨
        assert uid not in hub._speech_ended_ids

        await hub.stop()


class TestAwaitingVadEndTimeout:
    """Test awaiting_vad_end timeout mechanism."""

    @pytest.mark.asyncio
    async def test_awaiting_vad_end_timeout_clears_state(self):
        """타임아웃 후 awaiting_vad_end가 클리어되어야 함."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_awaiting_vad_timeout_s=0.1,  # 100ms 타임아웃
        )

        uid = uuid4()

        # SpeechEnd 없이 STT Final 전송
        transcript = Transcript(
            utterance_id=uid, text="테스트", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        # awaiting_vad_end=True 확인
        buffer = hub._merge_buffer
        assert buffer is not None
        assert buffer.awaiting_vad_end is True

        # 타임아웃 대기 (150ms)
        await asyncio.sleep(0.15)

        # 타임아웃으로 클리어됨
        assert buffer.awaiting_vad_end is False

        await hub.stop()

    @pytest.mark.asyncio
    async def test_timeout_cancelled_when_speech_end_arrives(self):
        """SpeechEnd가 오면 타임아웃이 취소되어야 함."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_awaiting_vad_timeout_s=0.5,  # 500ms 타임아웃
        )

        uid = uuid4()

        # STT Final 전송 → 타임아웃 시작
        transcript = Transcript(
            utterance_id=uid, text="테스트", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        buffer = hub._merge_buffer
        assert buffer is not None
        assert buffer.awaiting_vad_timeout_task is not None

        # SpeechEnd 전송 → 타임아웃 취소
        await hub.handle_vad_event(SpeechEnd(uid))

        # 타임아웃 태스크가 취소됨
        assert buffer.awaiting_vad_timeout_task is None

        await hub.stop()


class TestLowLatencyCommitBlocking:
    """Test commit blocking scenarios in low-latency mode."""

    @pytest.mark.asyncio
    async def test_normal_speech_commits_without_delay(self):
        """정상 발화는 지연 없이 커밋되어야 함 (regression)."""
        clock = FakeClock(initial_time=10.0)
        osc = FakeOscQueue()
        hub = ClientHub(
            stt=None,
            llm=None,  # 번역 없이 직접 커밋
            osc=osc,
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=10,  # 짧은 grace period
        )

        uid = uuid4()

        # 정상 시퀀스: SpeechStart → SpeechChunks → SpeechEnd → STT Final
        await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid, chunk=samples(0.5)))
        await hub.handle_vad_event(SpeechEnd(uid))

        # STT Final
        transcript = Transcript(
            utterance_id=uid, text="정상 발화", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript)

        # grace period 대기
        await asyncio.sleep(0.02)

        # OSC 메시지 전송됨
        assert len(osc.messages) == 1
        assert "정상 발화" in osc.messages[0].text

        await hub.stop()

    @pytest.mark.asyncio
    async def test_speech_end_after_commit_pop_does_not_block(self):
        """이전 커밋에서 pop된 후 SpeechEnd가 와도 블록되지 않아야 함.

        이것은 99be2bfc 버그의 핵심 시나리오입니다:
        1. 첫 번째 버퍼가 utterance_id를 포함하여 커밋됨 (_utterance_start_times pop)
        2. 같은 utterance_id의 SpeechEnd가 나중에 도착
        3. 새 버퍼에서 같은 utterance_id의 STT Final이 도착
        4. _utterance_start_times.get() = None이지만 SpeechEnd가 이미 왔으므로 post_end 처리
        """
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=None,
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=0,
        )

        uid1 = uuid4()
        uid2 = uuid4()

        # 첫 번째 발화 완료 및 커밋
        await hub.handle_vad_event(SpeechEnd(uid1))
        transcript1 = Transcript(
            utterance_id=uid1, text="첫 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript1)

        # uid1이 _utterance_start_times에서 pop됨
        assert uid1 not in hub._utterance_start_times

        # 하지만 _speech_ended_ids에는 있음 (커밋 시 정리되었지만, 다시 추가될 수 있음)
        # 실제로는 첫 번째 커밋에서 정리되었을 것이므로 없음

        # uid2로 새 발화 시작
        await hub.handle_vad_event(SpeechEnd(uid2))
        transcript2 = Transcript(
            utterance_id=uid2, text="두 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript2)

        # 정상적으로 커밋됨 (블록 없음)
        assert hub._merge_buffer is None

        await hub.stop()


class TestLowLatencyMergeOverlap:
    """Test relaxed overlap merge behavior."""

    def test_relaxed_overlap_strips_boundary_punct(self):
        hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())
        merged = hub._merge_with_overlap("같으면서.", "같으면서도 안.")
        assert merged == "같으면서도 안."

    def test_relaxed_overlap_min_length(self):
        hub = ClientHub(stt=None, llm=None, osc=FakeOscQueue())
        merged = hub._merge_with_overlap("가다.", "가다고")
        assert merged == "가다.가다고"


class TestResumeEndTimeout:
    """Test resume_confirmed timeout when STT Final doesn't arrive (Pattern A)."""

    @pytest.mark.asyncio
    async def test_resume_confirmed_without_stt_final_times_out(self):
        """resume_confirmed 상태에서 STT Final 안 오면 타임아웃 후 커밋."""
        clock = FakeClock(initial_time=10.0)
        osc = FakeOscQueue()
        hub = ClientHub(
            stt=None,
            llm=None,  # 번역 없이 직접 커밋
            osc=osc,
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=5000,  # 긴 grace period (커밋 안 되게)
            low_latency_awaiting_vad_timeout_s=0.1,  # 100ms 타임아웃
        )

        uid1 = uuid4()
        uid2 = uuid4()

        # 1. 첫 번째 발화 시작 및 STT Final
        await hub.handle_vad_event(SpeechStart(uid1, pre_roll=samples(0.0), chunk=samples(1.0)))
        await hub.handle_vad_event(SpeechEnd(uid1))
        transcript1 = Transcript(
            utterance_id=uid1, text="첫 번째 발화", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript1)

        # 버퍼에 텍스트가 있음 (grace period가 길어서 아직 커밋 안 됨)
        buffer = hub._merge_buffer
        assert buffer is not None
        assert "첫 번째 발화" in hub._merge_text(buffer.parts)

        # 2. 두 번째 발화 (resume) - SpeechStart
        await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
        assert buffer.resume_pending is True

        # 3. SpeechChunk 3개 → resume_confirmed
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid2, chunk=samples(0.5)))
        assert buffer.resume_confirmed is True

        # 4. SpeechEnd (STT Final 없이) → 타임아웃 시작
        await hub.handle_vad_event(SpeechEnd(uid2))
        assert buffer.resume_end_timeout_task is not None
        assert buffer.resume_end_utterance_id == uid2

        # 5. 타임아웃 대기 (150ms)
        await asyncio.sleep(0.15)

        # 6. 타임아웃으로 커밋됨
        assert hub._merge_buffer is None
        assert len(osc.messages) == 1
        assert "첫 번째 발화" in osc.messages[0].text

        await hub.stop()

    @pytest.mark.asyncio
    async def test_resume_end_timeout_cancelled_when_stt_final_arrives(self):
        """STT Final이 오면 타임아웃 취소."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=None,
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=5000,  # 긴 grace period
            low_latency_awaiting_vad_timeout_s=0.5,  # 500ms 타임아웃
        )

        uid1 = uuid4()
        uid2 = uuid4()

        # 1. 첫 번째 발화
        await hub.handle_vad_event(SpeechStart(uid1, pre_roll=samples(0.0), chunk=samples(1.0)))
        await hub.handle_vad_event(SpeechEnd(uid1))
        transcript1 = Transcript(
            utterance_id=uid1, text="첫 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript1)

        buffer = hub._merge_buffer
        assert buffer is not None

        # 2. resume_confirmed 상태로 만들기
        await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid2, chunk=samples(0.5)))
        assert buffer.resume_confirmed is True

        # 3. SpeechEnd → 타임아웃 시작
        await hub.handle_vad_event(SpeechEnd(uid2))
        assert buffer.resume_end_timeout_task is not None

        # 4. STT Final 도착 → 타임아웃 취소 (via _clear_resume_state)
        transcript2 = Transcript(
            utterance_id=uid2, text="두 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript2)

        # resume 상태 클리어됨 → 타임아웃도 취소됨
        assert buffer.resume_end_timeout_task is None
        assert buffer.resume_confirmed is False

        await hub.stop()

    @pytest.mark.asyncio
    async def test_new_resume_cancels_previous_timeout(self):
        """새 resume 시작 시 이전 타임아웃 취소."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=None,
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=5000,  # 긴 grace period
            low_latency_awaiting_vad_timeout_s=0.5,  # 500ms 타임아웃
        )

        uid1 = uuid4()
        uid2 = uuid4()
        uid3 = uuid4()

        # 1. 첫 번째 발화
        await hub.handle_vad_event(SpeechStart(uid1, pre_roll=samples(0.0), chunk=samples(1.0)))
        await hub.handle_vad_event(SpeechEnd(uid1))
        transcript1 = Transcript(
            utterance_id=uid1, text="첫 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript1)

        buffer = hub._merge_buffer
        assert buffer is not None

        # 2. uid2로 resume_confirmed + SpeechEnd → 타임아웃 시작
        await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid2, chunk=samples(0.5)))
        await hub.handle_vad_event(SpeechEnd(uid2))

        old_timeout_task = buffer.resume_end_timeout_task
        assert old_timeout_task is not None
        assert buffer.resume_end_utterance_id == uid2

        # 3. uid3로 새 resume 시작 → 이전 타임아웃 취소
        await hub.handle_vad_event(SpeechStart(uid3, pre_roll=samples(0.0), chunk=samples(1.0)))

        # 이전 타임아웃 취소됨
        assert buffer.resume_end_timeout_task is None
        assert buffer.resume_end_utterance_id is None
        # 새 resume 상태
        assert buffer.resume_pending is True
        assert buffer.resume_utterance_id == uid3

        await hub.stop()

    @pytest.mark.asyncio
    async def test_timeout_only_triggers_for_matched_utterance_id(self):
        """타임아웃은 정확히 매칭되는 utterance_id에서만 트리거."""
        clock = FakeClock(initial_time=10.0)
        hub = ClientHub(
            stt=None,
            llm=None,
            osc=FakeOscQueue(),
            clock=clock,
            low_latency_mode=True,
            low_latency_finalize_wait_ms=5000,  # 긴 grace period
            low_latency_awaiting_vad_timeout_s=0.1,
        )

        uid1 = uuid4()
        uid2 = uuid4()

        # 1. 첫 번째 발화
        await hub.handle_vad_event(SpeechStart(uid1, pre_roll=samples(0.0), chunk=samples(1.0)))
        await hub.handle_vad_event(SpeechEnd(uid1))
        transcript1 = Transcript(
            utterance_id=uid1, text="첫 번째", is_final=True, created_at=clock.now()
        )
        await hub._handle_low_latency_final(transcript1)

        buffer = hub._merge_buffer
        assert buffer is not None

        # 2. uid2로 resume_confirmed
        await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
        for _ in range(3):
            await hub.handle_vad_event(SpeechChunk(uid2, chunk=samples(0.5)))

        # 3. 다른 uid의 SpeechEnd → 타임아웃 시작 안 됨
        await hub.handle_vad_event(SpeechEnd(uid1))  # uid1의 SpeechEnd
        assert buffer.resume_end_timeout_task is None

        # 4. 정확한 uid의 SpeechEnd → 타임아웃 시작
        await hub.handle_vad_event(SpeechEnd(uid2))
        assert buffer.resume_end_timeout_task is not None
        assert buffer.resume_end_utterance_id == uid2

        await hub.stop()


class TestSpecCommitPaths:
    @pytest.mark.asyncio
    async def test_commit_merge_reuses_spec_translation_when_text_matches(self):
        clock = FakeClock(initial_time=10.0)
        osc = FakeOscQueue()
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=osc,
            clock=clock,
            low_latency_mode=True,
        )
        uid = uuid4()
        merge_id = uuid4()
        buffer = _MergeBuffer(
            merge_id=merge_id,
            parts=["hello"],
            utterance_ids=[uid],
            start_time=clock.now(),
            last_end_time=clock.now(),
            spec_text="hello",
            spec_translation=Translation(utterance_id=merge_id, text="hola"),
        )
        hub._merge_buffer = buffer
        hub._utterance_start_times[uid] = clock.now()

        await hub._commit_merge(buffer, reason="spec_done")

        assert hub._merge_buffer is None
        assert len(osc.messages) == 1
        assert osc.messages[0].text == "hello (hola)"
        assert len(hub._translation_history) == 1

    @pytest.mark.asyncio
    async def test_try_commit_after_spec_skips_when_spec_text_differs(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        hub = ClientHub(
            stt=None,
            llm=FakeLLMProvider(),
            osc=FakeOscQueue(),
            clock=FakeClock(initial_time=10.0),
            low_latency_mode=True,
        )
        buffer = _MergeBuffer(
            merge_id=uuid4(),
            parts=["new"],
            spec_text="old",
            spec_translation=Translation(utterance_id=uuid4(), text="translated"),
        )
        hub._merge_buffer = buffer
        called: list[str] = []

        async def fake_commit(self, commit_buffer, *, reason: str):  # noqa: ANN001
            _ = (self, commit_buffer)
            called.append(reason)

        monkeypatch.setattr(ClientHub, "_commit_merge", fake_commit)

        await hub._try_commit_after_spec(buffer, reason="spec_done", allow_fallback=False)
        assert called == []

    @pytest.mark.asyncio
    async def test_commit_merge_blocks_while_resume_or_waiting_states(self):
        hub = ClientHub(
            stt=None,
            llm=None,
            osc=FakeOscQueue(),
            clock=FakeClock(initial_time=10.0),
            low_latency_mode=True,
        )
        buffer = _MergeBuffer(merge_id=uuid4(), parts=["text"], utterance_ids=[uuid4()])
        hub._merge_buffer = buffer

        buffer.resume_pending = True
        await hub._commit_merge(buffer, reason="blocked_resume")
        assert hub._merge_buffer is buffer

        buffer.resume_pending = False
        buffer.awaiting_vad_end = True
        await hub._commit_merge(buffer, reason="blocked_waiting")
        assert hub._merge_buffer is buffer

        buffer.awaiting_vad_end = False
        buffer.finalize_wait_task = asyncio.create_task(asyncio.sleep(0.1))
        await hub._commit_merge(buffer, reason="blocked_grace")
        assert hub._merge_buffer is buffer
        buffer.finalize_wait_task.cancel()
        await asyncio.gather(buffer.finalize_wait_task, return_exceptions=True)
