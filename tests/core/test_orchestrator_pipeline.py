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
        if sender.sent:
            break
        await asyncio.sleep(0.01)

    assert sender.sent == ["FINAL (TRANSLATED)"]
    await hub.stop()
