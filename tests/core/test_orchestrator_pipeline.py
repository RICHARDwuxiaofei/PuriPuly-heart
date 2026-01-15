from __future__ import annotations

import asyncio
from dataclasses import dataclass

from puripuly_heart.core.clock import FakeClock
from puripuly_heart.core.llm.provider import SemaphoreLLMProvider
from puripuly_heart.core.orchestrator.hub import ClientHub
from puripuly_heart.core.osc.smart_queue import SmartOscQueue
from puripuly_heart.core.stt.controller import ManagedSTTProvider
from puripuly_heart.core.vad.gating import SpeechChunk, SpeechEnd, SpeechStart
from puripuly_heart.domain.models import Translation
from tests.helpers.fakes import FakeSender, SpeechAwareFakeBackend, samples


@dataclass(slots=True)
class FakeLLM:
    async def translate(
        self,
        *,
        utterance_id,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
        context_pairs: list[dict[str, str]] | None = None,
    ) -> Translation:
        _ = (system_prompt, source_language, target_language, context, context_pairs)
        await asyncio.sleep(0.01)
        return Translation(utterance_id=utterance_id, text="TRANSLATED")

    async def close(self) -> None:
        pass


async def test_orchestrator_e2e_headless():
    clock = FakeClock()
    sender = FakeSender()
    osc = SmartOscQueue(sender=sender, clock=clock, ttl_s=100.0)

    stt = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )

    llm = SemaphoreLLMProvider(inner=FakeLLM(), semaphore=asyncio.Semaphore(1))
    hub = ClientHub(stt=stt, llm=llm, osc=osc, clock=clock)
    await hub.start(auto_flush_osc=False)

    uid = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await hub.handle_vad_event(SpeechChunk(uid, chunk=samples(0.0)))
    await hub.handle_vad_event(SpeechEnd(uid))

    # Wait for translation and OSC send
    for _ in range(50):
        if "FINAL (TRANSLATED)" in sender.sent:
            break
        await asyncio.sleep(0.01)

    assert "FINAL (TRANSLATED)" in sender.sent
    await hub.stop()


async def test_stt_connected_sends_promo_message():
    """STT 연결 성공 시 'PuriPuly ON!' 메시지 전송."""
    clock = FakeClock()
    sender = FakeSender()
    osc = SmartOscQueue(sender=sender, clock=clock, ttl_s=100.0)
    stt = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )
    llm = SemaphoreLLMProvider(inner=FakeLLM(), semaphore=asyncio.Semaphore(1))
    hub = ClientHub(stt=stt, llm=llm, osc=osc, clock=clock)
    await hub.start(auto_flush_osc=False)

    # STT 연결 트리거
    uid = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0.05)

    assert "PuriPuly ON!" in sender.sent
    await hub.stop()


async def test_stt_promo_respects_interval():
    """5분 내 재연결 시 메시지 안 보냄."""
    clock = FakeClock()
    sender = FakeSender()
    osc = SmartOscQueue(sender=sender, clock=clock, ttl_s=100.0)
    stt = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )
    llm = SemaphoreLLMProvider(inner=FakeLLM(), semaphore=asyncio.Semaphore(1))
    hub = ClientHub(stt=stt, llm=llm, osc=osc, clock=clock)
    await hub.start(auto_flush_osc=False)

    uid = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0.05)

    initial_count = sender.sent.count("PuriPuly ON!")
    assert initial_count == 1

    # 세션 종료 후 4분 후 재연결 (5분 미만)
    await hub.stop()
    clock.advance(240.0)

    # 새 hub 인스턴스 (실제 앱에서는 같은 인스턴스지만, _last_promo_time은 유지됨)
    # 여기서는 hub._last_promo_time이 유지되므로 같은 인스턴스 재사용
    stt2 = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )
    hub.stt = stt2
    await hub.start(auto_flush_osc=False)

    uid2 = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0.05)

    # 메시지 추가 안 됨
    assert sender.sent.count("PuriPuly ON!") == 1
    await hub.stop()


async def test_stt_promo_sends_after_interval():
    """5분 후 재연결 시 메시지 다시 보냄."""
    clock = FakeClock()
    sender = FakeSender()
    osc = SmartOscQueue(sender=sender, clock=clock, ttl_s=100.0)
    stt = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )
    llm = SemaphoreLLMProvider(inner=FakeLLM(), semaphore=asyncio.Semaphore(1))
    hub = ClientHub(stt=stt, llm=llm, osc=osc, clock=clock)
    await hub.start(auto_flush_osc=False)

    uid = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0.05)

    assert sender.sent.count("PuriPuly ON!") == 1

    # 5분 후 재연결
    await hub.stop()
    clock.advance(301.0)

    stt2 = ManagedSTTProvider(
        backend=SpeechAwareFakeBackend(),
        sample_rate_hz=16000,
        clock=clock,
        reset_deadline_s=90.0,
    )
    hub.stt = stt2
    await hub.start(auto_flush_osc=False)

    uid2 = __import__("uuid").uuid4()
    await hub.handle_vad_event(SpeechStart(uid2, pre_roll=samples(0.0), chunk=samples(1.0)))
    await asyncio.sleep(0.05)

    # 메시지 다시 전송됨
    assert sender.sent.count("PuriPuly ON!") == 2
    await hub.stop()
